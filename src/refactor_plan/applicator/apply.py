from __future__ import annotations

import logging
from pathlib import Path

import rope.base.project as rp

logger = logging.getLogger(__name__)

from .file_moves import apply_file_move
from .import_rewrites import MoveRecord, rewrite_cross_cluster_imports
from .manifests import write_manifest
from .models import AppliedAction, ApplyResult, Escalation, MoveKind
from .symbol_moves import apply_symbol_move


def apply_plan(
    plan: dict,
    repo_root: Path,
    out_dir: Path,
    dry_run: bool = False,
) -> ApplyResult:
    result = ApplyResult()
    project = rp.Project(str(repo_root))

    try:
        # Phase 1: file moves
        for move in plan.get("file_moves", []):
            src_abs = Path(move["source"])
            dest_pkg_abs = Path(move["dest_package"])

            if dry_run:
                result.applied.append(
                    AppliedAction(
                        kind=MoveKind.FILE,
                        source=str(src_abs),
                        dest=str(dest_pkg_abs),
                    )
                )
                continue

            action = apply_file_move(project, src_abs, dest_pkg_abs)
            if isinstance(action, Escalation):
                result.failed.append(action)
            else:
                result.applied.append(action)

        if not dry_run:
            project.validate()

        # Phase 2: symbol moves
        for move in plan.get("symbol_moves", []):
            src_abs = Path(move["source"])
            dest_abs = Path(move["dest"])
            symbol_name = move["symbol"]

            if dry_run:
                result.applied.append(
                    AppliedAction(
                        kind=MoveKind.SYMBOL,
                        source=str(src_abs),
                        dest=str(dest_abs),
                        symbol=symbol_name,
                    )
                )
                continue

            action = apply_symbol_move(src_abs, dest_abs, symbol_name, repo_root, project)
            if isinstance(action, Escalation):
                result.failed.append(action)
            else:
                result.applied.append(action)

        if dry_run:
            return result

        # Phase 3: cross-cluster import post-pass
        move_records: list[MoveRecord] = []
        for action in result.applied:
            if action.kind == MoveKind.FILE:
                old_mod = _path_to_module(Path(action.source), repo_root)
                new_mod = _path_to_module(Path(action.dest), repo_root)
                if old_mod and new_mod:
                    move_records.append(MoveRecord(old_module=old_mod, new_module=new_mod, symbols=[]))
            elif action.kind == MoveKind.SYMBOL and action.symbol:
                old_mod = _path_to_module(Path(action.source), repo_root)
                new_mod = _path_to_module(Path(action.dest), repo_root)
                if old_mod and new_mod:
                    move_records.append(MoveRecord(old_module=old_mod, new_module=new_mod, symbols=[action.symbol]))

        if move_records:
            for py_file in repo_root.rglob("*.py"):
                try:
                    rewrite_cross_cluster_imports(py_file, move_records)
                except Exception as exc:
                    logger.debug("import rewrite failed for %s: %s", py_file, exc)

    finally:
        project.close()

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(result, out_dir)

    return result


def _path_to_module(path: Path, repo_root: Path) -> str | None:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return None
    parts = list(rel.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
        if parts[-1] == "__init__":
            parts = parts[:-1]
    return ".".join(parts) if parts else None
