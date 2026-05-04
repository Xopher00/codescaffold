from __future__ import annotations

import ast as _ast
import logging
from pathlib import Path

import libcst as cst
import rope.base.project as rp
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor
from refactor_plan.execution.result import AppliedAction, Escalation, MoveKind, MoveStrategy

logger = logging.getLogger(__name__)


def _dotted(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""


def _collect_symbol_names(symbol_code: str) -> set[str]:
    """Return every bare name referenced in the symbol body."""
    try:
        tree = _ast.parse(symbol_code)
        names: set[str] = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Name):
                names.add(node.id)
            elif isinstance(node, _ast.Attribute):
                val = node.value
                while isinstance(val, _ast.Attribute):
                    val = val.value
                if isinstance(val, _ast.Name):
                    names.add(val.id)
        return names
    except SyntaxError:
        return set()


def _needed_imports(
    src_tree: cst.Module, used_names: set[str]
) -> list[tuple[str, str | None, str | None]]:
    """Return (module, obj, asname) tuples for imports in src_tree that export a name in used_names.

    obj=None means a bare `import module` statement.
    """
    result: list[tuple[str, str | None, str | None]] = []
    for stmt in src_tree.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for small in stmt.body:
            if isinstance(small, cst.ImportFrom):
                if small.module is None or isinstance(small.names, cst.ImportStar):
                    continue
                mod = _dotted(small.module)
                for alias in small.names:
                    obj = alias.name.value if isinstance(alias.name, cst.Name) else _dotted(alias.name)
                    asname = None
                    if alias.asname is not None and isinstance(alias.asname, cst.AsName):
                        inner = alias.asname.name
                        asname = inner.value if isinstance(inner, cst.Name) else None
                    local_name = asname if asname else obj
                    if local_name in used_names:
                        result.append((mod, obj, asname))
            elif isinstance(small, cst.Import) and not isinstance(small.names, cst.ImportStar):
                for alias in small.names:  # type: ignore[union-attr]
                    mod = alias.name.value if isinstance(alias.name, cst.Name) else _dotted(alias.name)
                    asname = None
                    if alias.asname is not None and isinstance(alias.asname, cst.AsName):
                        inner = alias.asname.name
                        asname = inner.value if isinstance(inner, cst.Name) else None
                    local_name = asname if asname else mod
                    if local_name in used_names:
                        result.append((mod, None, asname))
    return result


def _file_to_module(path: Path, repo_root: Path) -> str:
    """Convert an absolute file path to a dotted module name relative to repo_root."""
    try:
        parts = list(path.relative_to(repo_root).parts)
    except ValueError:
        return ""
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
        if parts[-1] == "__init__":
            parts = parts[:-1]
    return ".".join(parts)


class _SymbolRemover(cst.CSTTransformer):
    """Remove a top-level FunctionDef or ClassDef by name.

    Guards against depth: only matches definitions directly in the module body,
    not methods inside classes or nested functions.
    """

    def __init__(self, symbol_name: str) -> None:
        self.symbol_name = symbol_name
        self.removed = False
        self._depth = 0

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._depth += 1
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef | cst.RemovalSentinel:
        self._depth -= 1
        if self._depth == 0 and updated_node.name.value == self.symbol_name and not self.removed:
            self.removed = True
            return cst.RemoveFromParent()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._depth += 1
        return True

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef | cst.RemovalSentinel:
        self._depth -= 1
        if self._depth == 0 and updated_node.name.value == self.symbol_name and not self.removed:
            self.removed = True
            return cst.RemoveFromParent()
        return updated_node


def _find_symbol_code(tree: cst.Module, symbol_name: str) -> str | None:
    for stmt in tree.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and stmt.name.value == symbol_name:
            return cst.Module(body=[stmt]).code
    return None


def _remove_symbol(tree: cst.Module, symbol_name: str) -> cst.Module:
    remover = _SymbolRemover(symbol_name)
    return tree.visit(remover)


def _organize_imports(file_path: Path, repo_root: Path, project: rp.Project | None = None) -> None:
    try:
        from rope.refactor.importutils import ImportOrganizer

        rope_rel = str(file_path.relative_to(repo_root))
        _owns_project = project is None
        if _owns_project:
            project = rp.Project(str(repo_root))
        try:
            resource = project.get_resource(rope_rel)
            organizer = ImportOrganizer(project)
            changes = organizer.organize_imports(resource)
            if changes:
                project.do(changes)
        finally:
            if _owns_project:
                project.close()
    except Exception as exc:
        # Import organization is best-effort; never crash the applicator
        logger.warning("import organization failed for %s: %s", file_path, exc)


def apply_symbol_move(
    src_abs: Path,
    dest_abs: Path,
    symbol_name: str,
    repo_root: Path,
    project: rp.Project | None = None,
) -> AppliedAction | Escalation:
    """Extract a named top-level symbol from src_abs into dest_abs using LibCST, carrying the necessary imports and cleaning up the source file."""
    _escalation_base = dict(
        kind=MoveKind.SYMBOL,
        source=str(src_abs),
        dest=str(dest_abs),
        symbol=symbol_name,
        category="symbol_move",
        strategy_attempted=MoveStrategy.LIBCST,
    )

    # Snapshot originals for rollback before touching anything
    original_content: dict[str, str] = {}
    try:
        original_content[str(src_abs)] = src_abs.read_text(encoding="utf-8")
    except OSError as exc:
        return Escalation(
            **_escalation_base,
            reason=f"Cannot read source file: {exc}",
        )

    if dest_abs.exists():
        try:
            original_content[str(dest_abs)] = dest_abs.read_text(encoding="utf-8")
        except OSError as exc:
            return Escalation(
                **_escalation_base,
                reason=f"Cannot read dest file: {exc}",
            )

    # Parse source
    src_text = original_content[str(src_abs)]
    try:
        src_tree = cst.parse_module(src_text)
    except cst.ParserSyntaxError as exc:
        return Escalation(
            **_escalation_base,
            reason=f"libcst parse error in source: {exc}",
        )

    # Extract the symbol's source code
    symbol_code = _find_symbol_code(src_tree, symbol_name)
    if symbol_code is None:
        return Escalation(
            **_escalation_base,
            reason=f"Symbol '{symbol_name}' not found in {src_abs}",
        )

    # Remove symbol from source tree
    modified_tree = _remove_symbol(src_tree, symbol_name)
    modified_src = modified_tree.code

    # Write modified source back
    try:
        src_abs.write_text(modified_src, encoding="utf-8")
    except OSError as exc:
        return Escalation(
            **_escalation_base,
            reason=f"Cannot write modified source: {exc}",
        )

    # Append symbol code to destination (create if missing), carrying needed imports.
    try:
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        existing = dest_abs.read_text(encoding="utf-8") if dest_abs.exists() else ""
        separator = "\n\n" if existing.rstrip() else ""
        combined = existing.rstrip() + separator + symbol_code

        # Carry imports from source that the symbol references,
        # excluding imports from the destination file itself (self-imports).
        dest_module = _file_to_module(dest_abs, repo_root)
        used_names = _collect_symbol_names(symbol_code)
        needed = [
            (mod, obj, asname)
            for mod, obj, asname in _needed_imports(src_tree, used_names)
            if mod != dest_module
        ]
        if needed:
            try:
                context = CodemodContext()
                tree = cst.parse_module(combined)
                for mod, obj, asname in needed:
                    AddImportsVisitor.add_needed_import(context, mod, obj, asname)
                tree = AddImportsVisitor(context).transform_module(tree)
                combined = tree.code
            except Exception as exc:
                logger.warning("import carry-over failed for %s: %s", dest_abs, exc)

        dest_abs.write_text(combined, encoding="utf-8")
    except OSError as exc:
        # Attempt rollback of source before returning failure
        try:
            src_abs.write_text(src_text, encoding="utf-8")
        except OSError:
            pass
        return Escalation(
            **_escalation_base,
            reason=f"Cannot write destination file: {exc}",
        )

    # Clean up unused imports in the modified source first — rope sees the
    # post-removal state and correctly strips imports the symbol took with it.
    _organize_imports(src_abs, repo_root, project)

    # After organizing, add a back-import if the symbol is still referenced in
    # the source (called by functions that weren't moved). Must come after
    # _organize_imports so rope's cached project doesn't strip it again.
    from refactor_plan.execution.import_rewrites import add_back_import
    dest_module = _file_to_module(dest_abs, repo_root)
    if dest_module:
        add_back_import(src_abs, symbol_name, dest_module)

    files_touched = [str(src_abs), str(dest_abs)]

    return AppliedAction(
        kind=MoveKind.SYMBOL,
        source=str(src_abs),
        dest=str(dest_abs),
        symbol=symbol_name,
        strategy=MoveStrategy.LIBCST,
        files_touched=files_touched,
        imports_rewritten=0,
        original_content=original_content,
        validation_passed=None,
    )
