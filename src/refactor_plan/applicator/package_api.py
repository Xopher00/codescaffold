from __future__ import annotations

import ast as _ast
import logging
from pathlib import Path

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor

from refactor_plan.execution import _make_project, rename_module, MoveRecord, add_back_import, rewrite_cross_cluster_imports

logger = logging.getLogger(__name__)


def _file_to_module(path: Path, repo_root: Path) -> str:
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


def _resolve_module_to_path(module: str, repo_root: Path) -> Path | None:
    """Resolve a dotted module string to an absolute .py file path if it exists locally."""
    parts = module.split(".")
    for base in [repo_root / "src", repo_root]:
        candidate = base.joinpath(*parts).with_suffix(".py")
        if candidate.exists():
            return candidate.resolve()
        pkg_init = base.joinpath(*parts) / "__init__.py"
        if pkg_init.exists():
            return pkg_init.resolve()
    return None


def _dotted_cst(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted_cst(node.value)}.{node.attr.value}"
    return ""


def _build_init_symbol_map(init_path: Path) -> dict[str, str]:
    """Parse __init__.py → {exported_symbol: source_submodule_stem}."""
    mapping: dict[str, str] = {}
    try:
        tree = _ast.parse(init_path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ImportFrom):
                continue
            if node.level != 1 or not node.module:
                continue
            for alias in node.names:
                mapping[alias.asname or alias.name] = node.module
    except Exception:
        pass
    return mapping


class _IntraPackageImportNormalizer(cst.CSTTransformer):
    """Rewrite intra-package absolute imports to relative form.

    Two cases:
    1. `from pkg.submod import X` → `from .submod import X`
    2. `from pkg import X`        → `from .submod import X`
       (when __init__.py maps X to submod)
    """

    def __init__(self, pkg_dotted: str, init_symbol_map: dict[str, str]) -> None:
        self._pkg = pkg_dotted           # e.g. "refactor_plan.execution"
        self._init_map = init_symbol_map # symbol → submodule stem
        self.changed = False

    def leave_ImportFrom(
        self,
        original_node: cst.ImportFrom,
        updated_node: cst.ImportFrom,
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel:
        if updated_node.module is None:
            return updated_node
        if isinstance(updated_node.names, cst.ImportStar):
            return updated_node
        if updated_node.relative:
            return updated_node  # already relative

        mod = _dotted_cst(updated_node.module)

        # Case 1: from pkg.submod import X  (direct deep import)
        direct_prefix = self._pkg + "."
        if mod.startswith(direct_prefix):
            sub = mod[len(direct_prefix):]
            if "." not in sub:  # single level only
                self.changed = True
                return updated_node.with_changes(
                    relative=[cst.Dot()],
                    module=cst.Name(sub),
                )

        # Case 2: from pkg import X  (through __init__, must resolve to submod)
        if mod == self._pkg and not isinstance(updated_node.names, cst.ImportStar):
            names = list(updated_node.names)
            # Group symbols by their source submodule
            by_submod: dict[str, list[cst.ImportAlias]] = {}
            unresolved: list[cst.ImportAlias] = []
            for alias in names:
                sym = alias.name.value if isinstance(alias.name, cst.Name) else _dotted_cst(alias.name)
                sub = self._init_map.get(sym)
                if sub:
                    by_submod.setdefault(sub, []).append(alias)
                else:
                    unresolved.append(alias)

            if not by_submod:
                return updated_node  # nothing to rewrite

            self.changed = True
            # We can't return multiple statements from a leave_ method directly,
            # but for single-submod groups (the common case) we can rewrite in place.
            # Multi-submod splits are deferred — replace what we can, leave the rest.
            if len(by_submod) == 1 and not unresolved:
                sub = next(iter(by_submod))
                cleaned = [
                    a.with_changes(comma=cst.MaybeSentinel.DEFAULT) if i == len(names) - 1 else a
                    for i, a in enumerate(names)
                ]
                return updated_node.with_changes(
                    relative=[cst.Dot()],
                    module=cst.Name(sub),
                    names=cleaned,
                )

            # Mixed: rewrite the resolvable portion via AddImportsVisitor,
            # keep unresolved in the original statement.
            # (Full split requires Module-level rewrite; best-effort for now.)
            context = CodemodContext()
            for sub, aliases in by_submod.items():
                for alias in aliases:
                    sym = alias.name.value if isinstance(alias.name, cst.Name) else _dotted_cst(alias.name)
                    asname = None
                    if alias.asname and isinstance(alias.asname, cst.AsName):
                        inner = alias.asname.name
                        asname = inner.value if isinstance(inner, cst.Name) else None
                    AddImportsVisitor.add_needed_import(context, f".{sub}", sym, asname)

            if not unresolved:
                return cst.RemoveFromParent()
            cleaned = [
                a.with_changes(comma=cst.MaybeSentinel.DEFAULT) if i == len(unresolved) - 1 else a
                for i, a in enumerate(unresolved)
            ]
            return updated_node.with_changes(names=cleaned)

        return updated_node


def _normalize_intra_package(repo_root: Path) -> list[str]:
    """Rewrite absolute intra-package imports to relative form."""
    touched: list[str] = []
    for py_file in sorted(repo_root.rglob("*.py")):
        pkg_dir = py_file.parent
        init = pkg_dir / "__init__.py"
        if not init.exists():
            continue
        pkg_dotted = _file_to_module(init, repo_root)
        if not pkg_dotted:
            continue
        init_map = _build_init_symbol_map(init)
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = cst.parse_module(source)
            transformer = _IntraPackageImportNormalizer(pkg_dotted, init_map)
            new_tree = tree.visit(transformer)
            if transformer.changed:
                result = new_tree.code
                if result != source:
                    py_file.write_text(result, encoding="utf-8")
                    touched.append(str(py_file))
        except Exception as exc:
            logger.warning("intra-package normalization failed for %s: %s", py_file, exc)
    return touched


def _abs_package_dirs(py_file: Path, repo_root: Path) -> set[Path]:
    """Return the resolved package directories this file imports from (absolute imports only)."""
    pkg_dirs: set[Path] = set()
    try:
        tree = _ast.parse(py_file.read_text(encoding="utf-8"))
    except Exception:
        return pkg_dirs
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ImportFrom) or node.level or not node.module:
            continue
        mod_path = _resolve_module_to_path(node.module, repo_root)
        if mod_path:
            pkg_dirs.add(mod_path.parent.resolve())
        # Also handle package-level import: from pkg.sub import X → pkg dir
        parts = node.module.split(".")
        for i in range(len(parts), 0, -1):
            for base in [repo_root / "src", repo_root]:
                cand = base.joinpath(*parts[:i]) / "__init__.py"
                if cand.exists():
                    pkg_dirs.add(cand.parent.resolve())
                    break
    return pkg_dirs


def _intra_pkg_siblings(py_file: Path, pkg_dir: Path) -> list[Path]:
    """Return sibling .py files in pkg_dir that py_file imports from via relative imports."""
    siblings: list[Path] = []
    try:
        tree = _ast.parse(py_file.read_text(encoding="utf-8"))
    except Exception:
        return siblings
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        sibling = pkg_dir / f"{node.module}.py"
        if sibling.exists() and sibling != py_file:
            siblings.append(sibling)
    return siblings


def _caller_pkg_would_cycle(caller_pkg: Path, target_pkg: Path, repo_root: Path) -> bool:
    """True if a caller in caller_pkg routing imports through target_pkg/__init__ would cycle.

    Checks if any module directly exported by target_pkg/__init__ imports from caller_pkg.
    Pattern: caller in P → target_pkg/__init__ → some_mod imports from P → cycle when
    P/__init__ is loading (P/__init__ loads caller → caller loads target → target loads some_mod
    → some_mod needs P which is partial).
    """
    init = target_pkg / "__init__.py"
    if not init.exists():
        return False
    caller_pkg_r = caller_pkg.resolve()
    try:
        tree = _ast.parse(init.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            sub = target_pkg / f"{node.module}.py"
            if not sub.exists():
                continue
            for dep_pkg in _abs_package_dirs(sub, repo_root):
                if dep_pkg == caller_pkg_r:
                    return True
    except Exception:
        pass
    return False


def _would_create_init_cycle(submod_path: Path, pkg_dir: Path, repo_root: Path) -> bool:
    """True if adding `from .submod import X` to pkg/__init__.py would create a circular import.

    Detects the pattern: submod (or a relative sibling it imports) imports from external
    package Q, and some file in Q imports back from pkg.
    """
    pkg_dir_r = pkg_dir.resolve()

    def _back_imports_pkg(f: Path) -> bool:
        for dep_pkg in _abs_package_dirs(f, repo_root):
            if dep_pkg == pkg_dir_r:
                continue  # same package — not a back-reference
            dep_init = Path(dep_pkg) / "__init__.py"
            if not dep_init.exists():
                continue
            for stem in set(_build_init_symbol_map(dep_init).values()):
                dep_file = Path(dep_pkg) / f"{stem}.py"
                if not dep_file.exists():
                    continue
                try:
                    if pkg_dir_r in _abs_package_dirs(dep_file, repo_root):
                        return True
                except Exception:
                    pass
        return False

    if _back_imports_pkg(submod_path):
        return True
    for sibling in _intra_pkg_siblings(submod_path, pkg_dir):
        if _back_imports_pkg(sibling):
            return True
    return False


def update_package_init(dest_abs: Path, symbol_name: str) -> None:
    """Add `from .{module} import {symbol}` to the destination package __init__.py."""
    init_path = dest_abs.parent / "__init__.py"
    line = f"from .{dest_abs.stem} import {symbol_name}"

    text = init_path.read_text(encoding="utf-8") if init_path.exists() else ""
    if any(line == ln.strip() for ln in text.splitlines()):
        return
    separator = "\n" if text.strip() else ""
    init_path.write_text(text.rstrip() + separator + line + "\n", encoding="utf-8")


def rewrite_symbol_callers(
    src_abs: Path,
    dest_abs: Path,
    symbol_name: str,
    src_module: str,
    dest_package: str,
    repo_root: Path,
) -> list[str]:
    """Rewrite all imports of symbol_name from src_module to use dest_package.

    src_abs gets a package-level back-import if the symbol is still referenced.
    All other files with `from src_module import symbol_name` are rewritten to
    `from dest_package import symbol_name`.
    """
    touched: list[str] = []
    move = MoveRecord(old_module=src_module, new_module=dest_package, symbols=[symbol_name])

    for py_file in sorted(repo_root.rglob("*.py")):
        if py_file == dest_abs:
            continue
        try:
            if py_file == src_abs:
                if add_back_import(py_file, symbol_name, dest_package):
                    touched.append(str(py_file))
            else:
                if rewrite_cross_cluster_imports(py_file, [move]):
                    touched.append(str(py_file))
        except Exception as exc:
            logger.warning("caller rewrite failed for %s: %s", py_file, exc)

    return touched


def _has_external_importers(mod_file: Path, pkg_dir: Path, repo_root: Path) -> bool:
    """True if any file outside pkg_dir imports this module — directly or through __init__."""
    mod_dotted = _file_to_module(mod_file, repo_root)
    if not mod_dotted:
        return False
    pkg_dotted = _file_to_module(pkg_dir / "__init__.py", repo_root)
    init_map = _build_init_symbol_map(pkg_dir / "__init__.py")
    serving_symbols = {sym for sym, stem in init_map.items() if stem == mod_file.stem}
    pkg_dir_r = pkg_dir.resolve()

    for py_file in repo_root.rglob("*.py"):
        try:
            py_file.resolve().relative_to(pkg_dir_r)
            continue
        except ValueError:
            pass
        try:
            tree = _ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ImportFrom) or not node.module or node.level:
                continue
            if node.module == mod_dotted:
                return True
            if node.module == pkg_dotted and node.names:
                for alias in node.names:
                    if alias.name in serving_symbols:
                        return True
    return False


def _is_module_public(
    mod_file: Path,
    pkg_dir: Path,
    repo_root: Path,
    externally_imported: set[str],
    project: "rp.Project",
) -> bool:
    """True if any code outside pkg_dir references this module.

    Combines two signals:
    1. externally_imported set (AST scan covering both `from pkg.mod import X`
       and `from pkg import X` resolved through __init__)
    2. Rope's Rename.get_changes (catches references rope can resolve)
    """
    from rope.base import libutils
    from rope.refactor.rename import Rename

    mod_dotted = _file_to_module(mod_file, repo_root)
    if mod_dotted in externally_imported:
        return True

    try:
        resource = libutils.path_to_resource(project, str(mod_file))
        renamer = Rename(project, resource)
        changes = renamer.get_changes(f"_{mod_file.stem}")
        pkg_dir_r = pkg_dir.resolve()
        for c in changes.changes:
            ref_path = (Path(project.root.real_path) / c.resource.path).resolve()
            try:
                ref_path.relative_to(pkg_dir_r)
            except ValueError:
                return True
    except Exception:
        pass

    return False


def privatize_if_internal(mod_file: Path, repo_root: Path) -> str | None:
    """If mod_file has no external importers, rename it with _ prefix and strip its __init__ entry.

    Returns the new path if renamed, None otherwise. Safe to call after a symbol
    move to check if the source file became private.
    """
    if mod_file.stem.startswith("_"):
        return None
    pkg_dir = mod_file.parent
    if not (pkg_dir / "__init__.py").exists():
        return None
    if _has_external_importers(mod_file, pkg_dir, repo_root):
        return None

    from refactor_plan.execution.rope_rename import _make_project, rename_module

    _strip_init_reexports(pkg_dir / "__init__.py", mod_file.stem)
    project = _make_project(repo_root)
    try:
        result = rename_module(repo_root, mod_file, f"_{mod_file.stem}", project=project)
    finally:
        project.close()
    if hasattr(result, "dest"):
        return result.dest
    return None


def _strip_init_reexports(init_path: Path, stem: str) -> bool:
    """Remove `from .stem import ...` lines from __init__.py. Returns True if modified."""
    try:
        source = init_path.read_text(encoding="utf-8")
        tree = cst.parse_module(source)
    except Exception:
        return False

    class _Stripper(cst.CSTTransformer):
        def __init__(self) -> None:
            self.removed = False

        def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.BaseSmallStatement | cst.RemovalSentinel:
            if not updated_node.relative or updated_node.module is None:
                return updated_node
            if isinstance(updated_node.module, cst.Name) and updated_node.module.value == stem:
                self.removed = True
                return cst.RemoveFromParent()
            return updated_node

    stripper = _Stripper()
    new_tree = tree.visit(stripper)
    if stripper.removed:
        init_path.write_text(new_tree.code, encoding="utf-8")
    return stripper.removed


def _prepend_underscores(repo_root: Path, externally_imported: set[str]) -> list[str]:
    """Rename purely-internal modules: foo.py → _foo.py.

    A module is internal if no file outside its package references it —
    not directly, not through __init__, not via any import path.

    Only operates within the detected source root (e.g. src/) — never
    touches tests, fixtures, or top-level scripts.
    """
    from refactor_plan.layout import detect_layout

    layout = detect_layout(repo_root)
    src_root = layout.source_root
    touched: list[str] = []

    project = _make_project(repo_root)
    try:
        for init_path in sorted(src_root.rglob("__init__.py")):
            pkg_dir = init_path.parent
            if pkg_dir.parent == src_root:
                continue  # top-level package — contains entrypoints, not internals

            for mod_file in sorted(pkg_dir.glob("*.py")):
                if mod_file.name == "__init__.py":
                    continue
                if mod_file.stem.startswith("_"):
                    continue
                if _is_module_public(mod_file, pkg_dir, repo_root, externally_imported, project):
                    continue
                if _strip_init_reexports(init_path, mod_file.stem):
                    s = str(init_path)
                    if s not in touched:
                        touched.append(s)
                result = rename_module(repo_root, mod_file, f"_{mod_file.stem}", project=project)
                if hasattr(result, "dest"):
                    touched.append(result.dest)
                    touched.extend(getattr(result, "files_touched", []))
                project.validate()
    finally:
        project.close()

    return touched


def normalize_package_imports(repo_root: Path) -> list[str]:
    """Enforce two import rules across the entire repo:

    1. Cross-package module-level imports → through package __init__:
       `from pkg.module import X`  (caller outside pkg/) → `from pkg import X`
       Also updates pkg/__init__.py with `from .module import X`.

    2. Intra-package imports → relative:
       `from pkg.module import X`  (caller inside pkg/) → `from .module import X`
       `from pkg import X`         (caller inside pkg/) → `from .module import X`
       (source submodule resolved from __init__.py re-export map)

    Safe to call repeatedly (idempotent). Returns list of files modified.
    """
    py_files = sorted(repo_root.rglob("*.py"))

    # Scan: find all modules referenced from outside their package.
    # Two forms:
    #   1. from pkg.module import X  → direct deep import (needs rewriting)
    #   2. from pkg import X         → already through __init__ (no rewrite needed,
    #      but the submodule serving X is still externally used)
    rewrites: dict[tuple[str, str], set[str]] = {}
    externally_imported: set[str] = set()

    for py_file in py_files:
        try:
            tree = _ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ImportFrom):
                continue
            if not node.module or not node.names or node.level:
                continue

            module_path = _resolve_module_to_path(node.module, repo_root)
            if module_path is None:
                continue

            pkg_dir = module_path.parent
            if not (pkg_dir / "__init__.py").exists():
                continue

            try:
                py_file.resolve().relative_to(pkg_dir.resolve())
                continue  # caller is inside the same package → handled by pass 2
            except ValueError:
                pass

            pkg_module = _file_to_module(pkg_dir / "__init__.py", repo_root)
            if not pkg_module:
                continue

            if pkg_module == node.module:
                # from pkg import X — already through __init__. Mark the serving
                # submodules as externally used (they're part of the public API).
                init_map = _build_init_symbol_map(pkg_dir / "__init__.py")
                for alias in node.names:
                    sym = alias.name
                    stem = init_map.get(sym)
                    if stem:
                        sub_file = pkg_dir / f"{stem}.py"
                        sub_mod = _file_to_module(sub_file, repo_root)
                        if sub_mod:
                            externally_imported.add(sub_mod)
                continue

            # from pkg.module import X — direct deep import, needs rewriting
            externally_imported.add(node.module)
            for alias in node.names:
                rewrites.setdefault((node.module, pkg_module), set()).add(alias.name)

    touched: list[str] = []
    for (src_module, pkg_module), symbols in rewrites.items():
        module_path = _resolve_module_to_path(src_module, repo_root)
        if module_path is None:
            continue
        if _would_create_init_cycle(module_path, module_path.parent, repo_root):
            logger.debug(
                "Skipping __init__ promotion for %s — would create circular import", src_module
            )
            continue
        for symbol in sorted(symbols):
            update_package_init(module_path, symbol)
        init_str = str(module_path.parent / "__init__.py")
        if init_str not in touched:
            touched.append(init_str)
        move = MoveRecord(old_module=src_module, new_module=pkg_module, symbols=sorted(symbols))
        target_pkg_dir = module_path.parent
        cycle_cache: dict[Path, bool] = {}
        for py_file in py_files:
            caller_pkg = py_file.parent.resolve()
            if caller_pkg not in cycle_cache:
                cycle_cache[caller_pkg] = _caller_pkg_would_cycle(caller_pkg, target_pkg_dir, repo_root)
            if cycle_cache[caller_pkg]:
                continue  # routing through __init__ would cycle for this caller's package
            try:
                if rewrite_cross_cluster_imports(py_file, [move]):
                    s = str(py_file)
                    if s not in touched:
                        touched.append(s)
            except Exception as exc:
                logger.warning("normalize rewrite failed for %s: %s", py_file, exc)

    # Pass 2: intra-package absolute → relative
    # Re-read py_files since pass 1 may have modified __init__.py files (new exports)
    for f in _normalize_intra_package(repo_root):
        if f not in touched:
            touched.append(f)

    # Pass 3: prepend _ to purely-internal modules (no external importers)
    for f in _prepend_underscores(repo_root, externally_imported):
        if f not in touched:
            touched.append(f)

    return touched
