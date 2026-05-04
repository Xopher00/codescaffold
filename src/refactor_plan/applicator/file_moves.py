from __future__ import annotations

from pathlib import Path

import rope.base.project as rp
from rope.base import libutils
from rope.base.exceptions import RefactoringError, ResourceNotFoundError
from rope.refactor.move import MoveModule
from refactor_plan.execution.result import AppliedAction, Escalation, MoveKind, MoveStrategy


def apply_file_move(
    project: rp.Project,
    src_abs: Path,
    dest_pkg_abs: Path,
) -> AppliedAction | Escalation:
    project_root = Path(project.root.real_path)
    src_str = str(src_abs)
    dest_str = str(dest_pkg_abs)

    try:
        src_rel = str(src_abs.relative_to(project_root))
        dest_rel = str(dest_pkg_abs.relative_to(project_root))
    except ValueError as exc:
        return Escalation(
            kind=MoveKind.FILE,
            source=src_str,
            dest=dest_str,
            reason=f"path not under project root {project_root}: {exc}",
            category="path_resolution",
            strategy_attempted=MoveStrategy.ROPE,
        )

    try:
        src_resource = libutils.path_to_resource(project, str(src_abs))
    except (ResourceNotFoundError, Exception) as exc:
        return Escalation(
            kind=MoveKind.FILE,
            source=src_str,
            dest=dest_str,
            reason=f"rope could not resolve source: {src_str} — {exc}",
            category="file_move",
            strategy_attempted=MoveStrategy.ROPE,
        )

    try:
        dest_resource = libutils.path_to_resource(project, str(dest_pkg_abs), type="folder")
    except (ResourceNotFoundError, Exception) as exc:
        return Escalation(
            kind=MoveKind.FILE,
            source=src_str,
            dest=dest_str,
            reason=f"rope could not resolve destination: {dest_str} — {exc}",
            category="file_move",
            strategy_attempted=MoveStrategy.ROPE,
        )

    if src_resource is None or dest_resource is None:
        return Escalation(
            kind=MoveKind.FILE,
            source=src_str,
            dest=dest_str,
            reason=f"rope returned None for resource (src={src_rel}, dest={dest_rel})",
            category="file_move",
            strategy_attempted=MoveStrategy.ROPE,
        )

    try:
        mover = MoveModule(project, src_resource)
        changes = mover.get_changes(dest_resource)
        project.do(changes)
    except RefactoringError as exc:
        return Escalation(
            kind=MoveKind.FILE,
            source=src_str,
            dest=dest_str,
            reason=str(exc),
            category="file_move",
            strategy_attempted=MoveStrategy.ROPE,
        )
    except Exception as exc:
        return Escalation(
            kind=MoveKind.FILE,
            source=src_str,
            dest=dest_str,
            reason=f"unexpected error during file move: {exc}",
            category="file_move",
            strategy_attempted=MoveStrategy.ROPE,
        )

    files_touched = [c.resource.path for c in changes.changes]

    return AppliedAction(
        kind=MoveKind.FILE,
        source=src_str,
        dest=dest_str,
        strategy=MoveStrategy.ROPE,
        files_touched=files_touched,
        imports_rewritten=max(0, len(files_touched) - 1),
    )
