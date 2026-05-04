from __future__ import annotations

import logging
import shutil
from pathlib import Path

import rope.base.project as rp
from rope.base import libutils
from rope.base.exceptions import RefactoringError
from rope.refactor.rename import Rename

from refactor_plan.naming.namer import RenameMap
from refactor_plan.planning.planner import RefactorPlan
from refactor_plan.execution.import_rewrites import MoveRecord, rewrite_cross_cluster_imports
from refactor_plan.execution.models import AppliedAction, ApplyResult, Escalation, MoveKind, MoveStrategy
from refactor_plan.records.manifests import write_manifest

logger = logging.getLogger(__name__)


def apply_rename_map(
    rename_map: RenameMap,
    refactor_plan: RefactorPlan,
    repo_root: Path,
    out_dir: Path,
    dry_run: bool = False,
) -> ApplyResult:
    result = ApplyResult()

    cluster_pkg = {
        Path(c.proposed_package).name: Path(c.proposed_package)
        for c in refactor_plan.clusters
        if c.proposed_package
    }

    project = rp.Project(str(repo_root))
    try:
        for entry in rename_map.entries:
            old_name = entry.old_name
            new_name = entry.new_name

            pkg_dir = cluster_pkg.get(old_name)
            if pkg_dir is None:
                result.failed.append(Escalation(
                    kind=MoveKind.PACKAGE,
                    source=old_name,
                    dest=new_name,
                    reason=f"No cluster found for '{old_name}' in plan",
                    category="name_apply",
                ))
                continue

            if not pkg_dir.exists():
                result.skipped.append(Escalation(
                    kind=MoveKind.PACKAGE,
                    source=str(pkg_dir),
                    dest=new_name,
                    reason=f"Package directory does not exist: {pkg_dir}",
                    category="name_apply",
                ))
                continue

            dest_dir = pkg_dir.parent / new_name

            if dry_run:
                result.applied.append(AppliedAction(
                    kind=MoveKind.PACKAGE,
                    source=str(pkg_dir),
                    dest=str(dest_dir),
                ))
                continue

            action = _rename_with_rope(project, pkg_dir, new_name)
            if isinstance(action, Escalation):
                logger.warning(
                    "rope Rename failed for %s → %s: %s — falling back to shutil+LibCST",
                    old_name, new_name, action.reason,
                )
                action = _rename_with_fallback(pkg_dir, new_name, repo_root)

            if isinstance(action, Escalation):
                result.failed.append(action)
            else:
                result.applied.append(action)
    finally:
        project.close()

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(result, out_dir)

    return result


def _rename_with_rope(
    project: rp.Project,
    pkg_dir: Path,
    new_name: str,
) -> AppliedAction | Escalation:
    init_py = pkg_dir / "__init__.py"
    dest_dir = pkg_dir.parent / new_name

    if not init_py.exists():
        return Escalation(
            kind=MoveKind.PACKAGE,
            source=str(pkg_dir),
            dest=str(dest_dir),
            reason=f"No __init__.py in {pkg_dir}",
            category="name_apply",
            strategy_attempted=MoveStrategy.ROPE,
        )

    try:
        resource = libutils.path_to_resource(project, str(init_py))
    except Exception as exc:
        return Escalation(
            kind=MoveKind.PACKAGE,
            source=str(pkg_dir),
            dest=str(dest_dir),
            reason=f"rope resource resolution failed: {exc}",
            category="name_apply",
            strategy_attempted=MoveStrategy.ROPE,
        )

    try:
        renamer = Rename(project, resource)
        changes = renamer.get_changes(new_name)
        project.do(changes)
    except RefactoringError as exc:
        return Escalation(
            kind=MoveKind.PACKAGE,
            source=str(pkg_dir),
            dest=str(dest_dir),
            reason=f"rope RefactoringError: {exc}",
            category="name_apply",
            strategy_attempted=MoveStrategy.ROPE,
        )
    except Exception as exc:
        return Escalation(
            kind=MoveKind.PACKAGE,
            source=str(pkg_dir),
            dest=str(dest_dir),
            reason=f"rope Rename failed: {exc}",
            category="name_apply",
            strategy_attempted=MoveStrategy.ROPE,
        )

    files_touched = [c.resource.path for c in changes.changes]
    return AppliedAction(
        kind=MoveKind.PACKAGE,
        source=str(pkg_dir),
        dest=str(dest_dir),
        strategy=MoveStrategy.ROPE,
        files_touched=files_touched,
        imports_rewritten=max(0, len(files_touched) - 1),
    )


def _rename_with_fallback(
    pkg_dir: Path,
    new_name: str,
    repo_root: Path,
) -> AppliedAction | Escalation:
    old_name = pkg_dir.name
    dest_dir = pkg_dir.parent / new_name

    # Snapshot files referencing old_name (for rollback)
    original_content: dict[str, str] = {}
    for py in repo_root.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
            if old_name in text or py.is_relative_to(pkg_dir):
                original_content[str(py)] = text
        except OSError:
            pass

    try:
        shutil.move(str(pkg_dir), str(dest_dir))
    except Exception as exc:
        return Escalation(
            kind=MoveKind.PACKAGE,
            source=str(pkg_dir),
            dest=str(dest_dir),
            reason=f"shutil.move failed: {exc}",
            category="name_apply",
            strategy_attempted=MoveStrategy.LIBCST,
        )

    # Build move records: package root + every submodule
    move_records: list[MoveRecord] = [
        MoveRecord(old_module=old_name, new_module=new_name, symbols=[])
    ]
    for py in dest_dir.rglob("*.py"):
        if py.name == "__init__.py":
            continue
        try:
            rel = py.relative_to(dest_dir)
        except ValueError:
            continue
        submod = str(rel.with_suffix("")).replace("/", ".")
        move_records.append(MoveRecord(
            old_module=f"{old_name}.{submod}",
            new_module=f"{new_name}.{submod}",
            symbols=[],
        ))

    imports_rewritten = 0
    for py_file in repo_root.rglob("*.py"):
        try:
            if rewrite_cross_cluster_imports(py_file, move_records):
                imports_rewritten += 1
        except Exception as exc:
            logger.warning("import rewrite failed for %s: %s", py_file, exc)

    files_touched = [str(p) for p in dest_dir.rglob("*.py")]
    return AppliedAction(
        kind=MoveKind.PACKAGE,
        source=str(pkg_dir),
        dest=str(dest_dir),
        strategy=MoveStrategy.LIBCST,
        files_touched=files_touched,
        imports_rewritten=imports_rewritten,
        original_content=original_content,
    )
