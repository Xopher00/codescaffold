from __future__ import annotations

import ast
import logging
from pathlib import Path

import rope.base.project as rp
from rope.base import libutils
from rope.base.exceptions import RefactoringError
from rope.refactor.rename import Rename
from refactor_plan.execution.models import AppliedAction, Escalation, MoveKind, MoveStrategy

logger = logging.getLogger(__name__)


def rename_symbol(
    repo_root: Path,
    file_path: Path,
    symbol_name: str,
    new_name: str,
    dry_run: bool = False,
) -> AppliedAction | Escalation:
    """Rename a function or class at its definition, propagating to all call sites."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return Escalation(
            kind=MoveKind.SYMBOL,
            source=str(file_path),
            symbol=symbol_name,
            reason=f"Cannot read file: {exc}",
            category="rename",
        )

    offset = _find_symbol_offset(source, symbol_name)
    if offset is None:
        return Escalation(
            kind=MoveKind.SYMBOL,
            source=str(file_path),
            symbol=symbol_name,
            reason=f"Symbol '{symbol_name}' not found in {file_path.name}",
            category="rename",
        )

    project = rp.Project(str(repo_root))
    try:
        resource = libutils.path_to_resource(project, str(file_path))
        renamer = Rename(project, resource, offset)
        changes = renamer.get_changes(new_name)
        files_touched = [c.resource.path for c in changes.changes]

        if not dry_run:
            project.do(changes)

        return AppliedAction(
            kind=MoveKind.SYMBOL,
            source=str(file_path),
            dest=str(file_path),
            symbol=symbol_name,
            strategy=MoveStrategy.ROPE,
            files_touched=files_touched,
            imports_rewritten=max(0, len(files_touched) - 1),
        )
    except RefactoringError as exc:
        return Escalation(
            kind=MoveKind.SYMBOL,
            source=str(file_path),
            symbol=symbol_name,
            reason=f"rope RefactoringError: {exc}",
            category="rename",
            strategy_attempted=MoveStrategy.ROPE,
        )
    except Exception as exc:
        return Escalation(
            kind=MoveKind.SYMBOL,
            source=str(file_path),
            symbol=symbol_name,
            reason=f"rope rename failed: {exc}",
            category="rename",
            strategy_attempted=MoveStrategy.ROPE,
        )
    finally:
        project.close()


def rename_module(
    repo_root: Path,
    module_path: Path,
    new_name: str,
    dry_run: bool = False,
) -> AppliedAction | Escalation:
    """Rename a module file or package directory, updating all imports."""
    if not module_path.exists():
        return Escalation(
            kind=MoveKind.FILE,
            source=str(module_path),
            reason=f"Path does not exist: {module_path}",
            category="rename",
        )

    if module_path.is_dir():
        init_py = module_path / "__init__.py"
        if not init_py.exists():
            return Escalation(
                kind=MoveKind.PACKAGE,
                source=str(module_path),
                reason=f"No __init__.py in {module_path} — not a Python package",
                category="rename",
            )
        resource_path = init_py
        kind = MoveKind.PACKAGE
        dest = str(module_path.parent / new_name)
    else:
        resource_path = module_path
        kind = MoveKind.FILE
        dest = str(module_path.parent / f"{new_name}.py")

    project = rp.Project(str(repo_root))
    try:
        resource = libutils.path_to_resource(project, str(resource_path))
        renamer = Rename(project, resource)
        changes = renamer.get_changes(new_name)
        files_touched = [c.resource.path for c in changes.changes]

        if not dry_run:
            project.do(changes)

        return AppliedAction(
            kind=kind,
            source=str(module_path),
            dest=dest,
            strategy=MoveStrategy.ROPE,
            files_touched=files_touched,
            imports_rewritten=max(0, len(files_touched) - 1),
        )
    except RefactoringError as exc:
        return Escalation(
            kind=kind,
            source=str(module_path),
            reason=f"rope RefactoringError: {exc}",
            category="rename",
            strategy_attempted=MoveStrategy.ROPE,
        )
    except Exception as exc:
        return Escalation(
            kind=kind,
            source=str(module_path),
            reason=f"rope rename failed: {exc}",
            category="rename",
            strategy_attempted=MoveStrategy.ROPE,
        )
    finally:
        project.close()


def _find_symbol_offset(source: str, symbol_name: str) -> int | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    lines = source.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines[:-1]:
        line_offsets.append(line_offsets[-1] + len(line))

    matches: list[tuple[int, int, int]] = []  # (lineno, col, absolute_offset)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name != symbol_name:
            continue
        lineno = node.lineno - 1
        if lineno >= len(lines):
            continue
        try:
            name_col = lines[lineno].index(symbol_name, node.col_offset)
        except ValueError:
            continue
        matches.append((node.lineno, node.col_offset, line_offsets[lineno] + name_col))

    if not matches:
        return None
    matches.sort(key=lambda m: (m[0], m[1]))
    return matches[0][2]
