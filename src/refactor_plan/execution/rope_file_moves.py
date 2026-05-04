from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rope.base.project import Project
from rope.base import libutils

from refactor_plan.interface.graphify_adapter import MoveCandidate


@dataclass(frozen=True)
class AppliedMove:
    source_path: Path
    target_package: str
    rope_description: str


def apply_move_candidates(
    *,
    repo_root: str | Path,
    candidates: list[MoveCandidate],
    dry_run: bool = True,
) -> list[AppliedMove]:
    project = Project(str(repo_root))
    applied: list[AppliedMove] = []

    try:
        for candidate in candidates:
            changes = _changes_for_file_move(project, candidate)

            description = changes.get_description()
            applied.append(
                AppliedMove(
                    source_path=candidate.source_path,
                    target_package=candidate.target_package,
                    rope_description=description,
                )
            )

            if not dry_run:
                project.do(changes)
                project.validate()

        return applied

    finally:
        project.close()


def _changes_for_file_move(project: Project, candidate: MoveCandidate):
    """Convert one approved graph-derived move candidate into Rope changes.

    Intentionally boring: no graph parsing, no architectural guessing.
    For file/module moves use MoveModule (updates imports).
    For symbol moves use rope.refactor.move.create_move(project, resource, offset).
    """
    from rope.refactor.move import MoveModule

    source = libutils.path_to_resource(project, str(candidate.source_path))
    if source is None:
        raise ValueError(f"Rope could not resolve source: {candidate.source_path}")

    target_folder_path = Path(*candidate.target_package.split("."))
    target_folder = libutils.path_to_resource(
        project,
        str(target_folder_path),
        type="folder",
    )
    if target_folder is None:
        raise ValueError(f"Rope could not resolve target package: {candidate.target_package}")

    mover = MoveModule(project, source)
    return mover.get_changes(target_folder)
