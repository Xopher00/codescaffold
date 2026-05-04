from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import rope.base.project as rp

from refactor_plan.applicator.file_moves import apply_file_move
from refactor_plan.applicator.symbol_moves import apply_symbol_move
from refactor_plan.execution.import_rewrites import MoveRecord, rewrite_cross_cluster_imports
from refactor_plan.execution.models import AppliedAction, ApplyResult, Escalation, MoveKind
from refactor_plan.records.manifests import write_manifest


logger = logging.getLogger(__name__)


def _ensure_package_inits(dest_dirs: set[Path], boundary: Path) -> list[Path]:
    """Create __init__.py in dest_dirs and ancestors up to (not including) boundary.

    Does not overwrite existing files.  Safe to call after file moves to make
    newly-created package directories importable.
    """
    created: list[Path] = []
    for dest_dir in sorted(dest_dirs):
        current = dest_dir
        while current != boundary and current != current.parent:
            current.mkdir(parents=True, exist_ok=True)
            init = current / "__init__.py"
            if not init.exists():
                init.touch()
                created.append(init)
            current = current.parent
    return created


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


def _run_import_rewrites(
    applied: list[AppliedAction],
    repo_root: Path,
    src_root: Path | None,
) -> tuple[list[MoveRecord], list[Escalation]]:
    """Phase 3: build move records and rewrite cross-cluster imports.

    Returns (move_records, skipped_escalations).
    """
    move_records: list[MoveRecord] = []
    for action in applied:
        if action.kind == MoveKind.FILE:
            src_path = Path(action.source)
            dest_file = Path(action.dest) / src_path.name
            old_mod = _path_to_module(src_path, repo_root, src_root)
            new_mod = _path_to_module(dest_file, repo_root, src_root)
            if old_mod and new_mod:
                move_records.append(MoveRecord(old_module=old_mod, new_module=new_mod, symbols=[]))
        elif action.kind == MoveKind.SYMBOL and action.symbol:
            old_mod = _path_to_module(Path(action.source), repo_root, src_root)
            new_mod = _path_to_module(Path(action.dest), repo_root, src_root)
            if old_mod and new_mod:
                move_records.append(MoveRecord(old_module=old_mod, new_module=new_mod, symbols=[action.symbol]))

    skipped: list[Escalation] = []
    if move_records:
        for py_file in repo_root.rglob("*.py"):
            try:
                rewrite_cross_cluster_imports(py_file, move_records)
            except Exception as exc:
                logger.warning("import rewrite failed for %s: %s", py_file, exc)
                skipped.append(Escalation(
                    kind=MoveKind.FILE,
                    source=str(py_file),
                    reason=str(exc),
                    category="import_rewrite",
                ))

    return move_records, skipped


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def apply_plan(
    plan: dict,
    repo_root: Path,
    out_dir: Path,
    dry_run: bool = False,
    stop_after: Literal["moves", "rewrites"] | None = None,
) -> ApplyResult:
    """Apply the refactor plan through explicit phases.

    stop_after="moves"   — run file moves + init creation, then stop.
    stop_after="rewrites" — run moves + init creation + import rewrites, then stop.
    stop_after=None      — run all phases (default).

    Callers that need to inject validation between phases should call
    _run_file_moves and _run_import_rewrites directly.
    """
    result = ApplyResult()

    # Deduplicate by source — planner can assign the same file to multiple clusters
    seen_sources: set[str] = set()
    file_moves: list[dict] = []
    for move in plan.get("file_moves", []):
        if move["source"] not in seen_sources:
            seen_sources.add(move["source"])
            file_moves.append(move)

    src_root_str = plan.get("source_root")
    src_root: Path | None = Path(src_root_str) if src_root_str else None

    project = rp.Project(str(repo_root))

    try:
        # Phase 1: file moves
        applied, failed, dest_dirs = _run_file_moves(project, file_moves, dry_run)
        result.applied.extend(applied)
        result.failed.extend(failed)

        if not dry_run:
            project.validate()

        # Phase 2: __init__.py creation
        if not dry_run and dest_dirs and src_root is not None:
            _ensure_package_inits(dest_dirs, src_root)

        if stop_after == "moves":
            return result

        # Phase 3: symbol moves
        for move in plan.get("symbol_moves", []):
            src_abs = Path(move["source"])
            dest_abs = Path(move["dest"])
            symbol_name = move["symbol"]

            if dry_run:
                result.applied.append(AppliedAction(
                    kind=MoveKind.SYMBOL,
                    source=str(src_abs),
                    dest=str(dest_abs),
                    symbol=symbol_name,
                ))
                continue

            action = apply_symbol_move(src_abs, dest_abs, symbol_name, repo_root, project)
            if isinstance(action, Escalation):
                result.failed.append(action)
            else:
                result.applied.append(action)

        if dry_run:
            return result

        # Phase 4: import rewrites
        _, skipped = _run_import_rewrites(result.applied, repo_root, src_root)
        result.skipped.extend(skipped)

    finally:
        project.close()

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(result, out_dir)

    return result


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
