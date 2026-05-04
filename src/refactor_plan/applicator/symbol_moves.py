from __future__ import annotations

import logging
from pathlib import Path

import libcst as cst
import rope.base.project as rp

from refactor_plan.applicator.execution.models import AppliedAction, Escalation, MoveKind, MoveStrategy

logger = logging.getLogger(__name__)


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

    # Append symbol code to destination (create if missing)
    try:
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        if dest_abs.exists():
            existing = dest_abs.read_text(encoding="utf-8")
            separator = "\n\n" if existing.rstrip() else ""
            dest_abs.write_text(existing.rstrip() + separator + symbol_code, encoding="utf-8")
        else:
            dest_abs.write_text(symbol_code, encoding="utf-8")
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

    # Clean up unused imports in the modified source
    _organize_imports(src_abs, repo_root, project)

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
