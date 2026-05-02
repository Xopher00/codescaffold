from __future__ import annotations

from pathlib import Path

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor


def ensure_future_annotations(source_path: Path) -> bool:
    source = source_path.read_text(encoding="utf-8")
    original = source

    tree = cst.parse_module(source)
    context = CodemodContext()
    AddImportsVisitor.add_needed_import(context, "__future__", "annotations")
    visitor = AddImportsVisitor(context)
    new_tree = tree.visit(visitor)
    result = new_tree.code

    if result == original:
        return False
    source_path.write_text(result, encoding="utf-8")
    return True


def is_residue(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return False

    if not source.strip():
        return True

    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return False

    for stmt in tree.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            return False
        for small_stmt in stmt.body:
            if not isinstance(small_stmt, (cst.Import, cst.ImportFrom)):
                return False

    return True


def find_stray_inits(repo_root: Path) -> list[Path]:
    stray: list[Path] = []
    for init in repo_root.rglob("__init__.py"):
        parent = init.parent
        has_py_sibling = any(
            p != init and p.suffix == ".py"
            for p in parent.iterdir()
        )
        if not has_py_sibling:
            stray.append(init)
    return stray


def pre_create_dest_module(dest_path: Path, src_root: Path) -> None:
    try:
        parents = dest_path.relative_to(src_root).parents
    except ValueError:
        return

    for rel_parent in reversed(list(parents)):
        if rel_parent == Path("."):
            continue
        pkg_dir = src_root / rel_parent
        init = pkg_dir / "__init__.py"
        if not init.exists():
            pkg_dir.mkdir(parents=True, exist_ok=True)
            init.touch()
