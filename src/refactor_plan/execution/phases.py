

from pathlib import Path
from refactor_plan.execution.result import Escalation, AppliedAction, MoveKind
import rope.base.project as rp
from refactor_plan.applicator.file_moves import apply_file_move

def _path_to_module(
    path: Path,
    repo_root: Path,
    src_root: Path | None = None,
) -> str | None:
    try:
        if src_root is not None:
            try:
                parts = list(path.relative_to(src_root).parts)
            except ValueError:
                parts = list(path.relative_to(repo_root).parts)
        else:
            rel = path.relative_to(repo_root)
            parts = list(rel.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
    except ValueError:
        return None

    if not parts:
        return None
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
        if parts[-1] == "__init__":
            parts = parts[:-1]
    return ".".join(parts) if parts else None



def _cleanup_empty_source_dirs(applied: list[AppliedAction], boundary: Path) -> list[Path]:
    """Remove source directories that became empty (or __init__.py-only) after file moves.

    Walks each moved file's source directory upward, removing dirs that have no
    remaining content besides a lone __init__.py, stopping at boundary.
    """
    removed: list[Path] = []
    candidate_dirs = {Path(a.source).parent for a in applied if a.kind == MoveKind.FILE}

    for src_dir in sorted(candidate_dirs, reverse=True):  # deepest first
        current = src_dir
        while current != boundary and current != current.parent:
            if not current.exists():
                current = current.parent
                continue
            non_init = [f for f in current.iterdir() if f.name != "__init__.py"]
            if non_init:
                break  # dir still has real content
            init = current / "__init__.py"
            if init.exists():
                init.unlink()
            try:
                current.rmdir()
                removed.append(current)
            except OSError:
                break  # not actually empty (race or non-empty subdir)
            current = current.parent

    return removed



# ---------------------------------------------------------------------------
# Phase functions
# ---------------------------------------------------------------------------

def _run_file_moves(
    project: rp.Project,
    file_moves: list[dict],
    dry_run: bool,
) -> tuple[list[AppliedAction], list[Escalation], set[Path]]:
    """Phase 1: apply file moves via rope.

    Returns (applied_actions, failures, dest_dirs).
    dest_dirs is the set of destination package directories that were created,
    used by the caller to seed __init__.py creation.
    """
    applied: list[AppliedAction] = []
    failed: list[Escalation] = []
    dest_dirs: set[Path] = set()

    for move in file_moves:
        src_abs = Path(move["source"])
        dest_pkg_abs = Path(move["dest_package"])

        if dry_run:
            applied.append(AppliedAction(
                kind=MoveKind.FILE,
                source=str(src_abs),
                dest=str(dest_pkg_abs),
            ))
            continue

        action = apply_file_move(project, src_abs, dest_pkg_abs)
        if isinstance(action, Escalation):
            failed.append(action)
        else:
            applied.append(action)
            dest_dirs.add(dest_pkg_abs)

    return applied, failed, dest_dirs
