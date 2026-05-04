from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import rope.base.project as rp
from refactor_plan.applicator.symbol_moves import apply_symbol_move
from refactor_plan.execution.import_rewrites import MoveRecord, add_back_import, rewrite_cross_cluster_imports
from refactor_plan.execution.models import AppliedAction, ApplyResult, Escalation, MoveKind
from refactor_plan.execution.phases import _run_file_moves, _path_to_module
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


def _run_import_rewrites(
    applied: list[AppliedAction],
    repo_root: Path,
    src_root: Path | None,
) -> tuple[list[MoveRecord], list[Escalation]]:
    """Phase 4: rewrite all cross-cluster import references across the repo to reflect completed file and symbol moves, returning move records and any escalated failures."""
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

    # Back-imports: if a symbol was moved out of a file but the file still
    # references it (type annotations, default values, etc.), add the new import.
    for action in applied:
        if action.kind != MoveKind.SYMBOL or not action.symbol:
            continue
        src_file = Path(action.source)
        if not src_file.exists():
            continue
        new_mod = _path_to_module(Path(action.dest), repo_root, src_root)
        if new_mod:
            try:
                add_back_import(src_file, action.symbol, new_mod)
            except Exception as exc:
                logger.warning("back-import failed for %s: %s", src_file, exc)

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
    """Apply an approved refactor plan through sequential phases: file moves, __init__.py creation, symbol moves, and cross-cluster import rewrites."""
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
