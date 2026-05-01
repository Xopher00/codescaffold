"""rope_runner: translate a RefactorPlan into rope refactoring operations.

Algorithm
---------
1. LibCST pre-flight (per affected source file):
   - Resolve byte offsets for every SymbolMove via ByteSpanPositionProvider.
     NOTE: offsets are computed AFTER file moves from the file's current
     location, because rope rewrites imports during MoveModule (changing the
     byte positions of symbols).
   - Enumerate references via ScopeProvider; flag accesses with empty
     referents as "no_referent" escalations (rope can't track string-form refs).

2. rope apply (per move):
   - Per FileMove: MoveModule to dest_pkg_path folder.
   - Per approved SymbolMove: create_move(project, resource, offset) → MoveGlobal.
   - After all moves: organize_imports per affected file.
   - Each project.do() is a separate history entry, tracked by index.

3. Rollback:
   - Reopen the rope project (history persists in .ropeproject).
   - Call project.history.undo() applied_count times.
   - Remove __init__.py files created by apply_plan (recorded in a manifest).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import libcst as cst
from libcst.metadata import (
    ByteSpanPositionProvider,
    MetadataWrapper,
    ScopeProvider,
)
from libcst.metadata.scope_provider import GlobalScope
from pydantic import BaseModel
from rope.base import libutils
from rope.base.project import Project
from rope.refactor.importutils import ImportOrganizer
from rope.refactor.move import MoveModule, create_move

from refactor_plan.planner import RefactorPlan

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class AppliedAction(BaseModel):
    kind: str            # "file_move" | "symbol_move" | "organize_imports"
    description: str     # human-readable
    history_index: int   # rope history entry index after this action


class Escalation(BaseModel):
    kind: str            # "string_form_ref" | "no_referent" | "offset_not_found"
    symbol_id: str
    detail: str


class ApplyResult(BaseModel):
    applied: list[AppliedAction]
    escalations: list[Escalation]


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------


def _resolve_src_path(repo_root: Path, src: str) -> Optional[Path]:
    """Resolve a plan src path to an existing absolute path.

    The plan's src is relative to wherever graphify was run, which may differ
    from repo_root (e.g., when using a fixture copy in tests).  Strategy:
    1. Try repo_root / src directly.
    2. Try repo_root joined with just the last N components, decreasing.
    3. Try repo_root.rglob for the basename (picks the first match).
    """
    direct = repo_root / src
    if direct.exists():
        return direct

    # Strip leading components and try progressively shorter suffixes
    parts = Path(src).parts
    for start in range(1, len(parts)):
        candidate = repo_root.joinpath(*parts[start:])
        if candidate.exists():
            return candidate

    # Last resort: rglob by basename
    basename = Path(src).name
    matches = list(repo_root.rglob(basename))
    if matches:
        return matches[0]

    return None


# ---------------------------------------------------------------------------
# LibCST pre-flight
# ---------------------------------------------------------------------------


def _symbol_name_from_label(label: str) -> str:
    """Strip trailing '()' from function labels to get the symbol name."""
    name = label.rstrip("()")
    # Also strip leading dot for method labels like '.echo()'
    return name.lstrip(".")


def _preflight_file(
    src_path: Path,
    symbol_labels: list[tuple[str, str]],  # [(symbol_id, label), ...]
) -> tuple[dict[tuple[str, str], int], list[Escalation]]:
    """Run LibCST analysis on one source file.

    Returns:
        offset_map: {(src_file_str, label) -> byte_offset_of_name}
        escalations: list of Escalation for unresolvable references
    """
    source = src_path.read_text(encoding="utf-8")
    module = cst.parse_module(source)
    wrapper = MetadataWrapper(module)

    spans = wrapper.resolve(ByteSpanPositionProvider)
    scopes = wrapper.resolve(ScopeProvider)

    # Build a map: symbol_name -> byte offset of its Name node
    name_to_offset: dict[str, int] = {}
    for node, span in spans.items():
        if isinstance(node, (cst.FunctionDef, cst.ClassDef)):
            name_node = node.name
            if name_node in spans:
                name_span = spans[name_node]
                name_to_offset[name_node.value] = name_span.start

    offset_map: dict[tuple[str, str], int] = {}
    escalations: list[Escalation] = []
    src_key = str(src_path)

    for symbol_id, label in symbol_labels:
        sym_name = _symbol_name_from_label(label)
        if sym_name in name_to_offset:
            offset_map[(src_key, label)] = name_to_offset[sym_name]
        else:
            escalations.append(
                Escalation(
                    kind="offset_not_found",
                    symbol_id=symbol_id,
                    detail=f"Could not find '{sym_name}' as FunctionDef/ClassDef in {src_path}",
                )
            )

    # Check for accesses with empty referents (string-form refs rope can't see)
    seen_global_scopes: set[int] = set()
    for scope in scopes.values():
        if isinstance(scope, GlobalScope) and id(scope) not in seen_global_scopes:
            seen_global_scopes.add(id(scope))
            for access in scope.accesses:
                if not access.referents:
                    # Flag symbols we're trying to move that have unresolvable refs
                    for symbol_id, label in symbol_labels:
                        sym_name = _symbol_name_from_label(label)
                        node = access.node
                        if isinstance(node, cst.Name) and node.value == sym_name:
                            escalations.append(
                                Escalation(
                                    kind="no_referent",
                                    symbol_id=symbol_id,
                                    detail=(
                                        f"Access to '{sym_name}' in {src_path} "
                                        "has no resolvable referents — "
                                        "possible string-form reference rope cannot track"
                                    ),
                                )
                            )

    return offset_map, escalations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _find_current_path(repo_root: Path, original_src: str) -> Optional[Path]:
    """Find the current location of a file that may have been moved by rope.

    After MoveModule, a file originally at 'messy_pkg/foo.py' may now live
    at 'pkg_NNN/foo.py'.  Search by basename across repo_root.
    """
    basename = Path(original_src).name
    # First: try the direct path (for files not yet moved)
    direct = _resolve_src_path(repo_root, original_src)
    if direct is not None and direct.exists():
        return direct
    # Second: search by basename (file was relocated by rope)
    candidates = [
        p for p in repo_root.rglob(basename)
        if p.is_file()
        and ".ropeproject" not in p.parts
        and "__pycache__" not in p.parts
    ]
    return candidates[0] if candidates else None


def apply_plan(
    plan: RefactorPlan,
    repo_root: Path,
    *,
    only_approved_symbols: bool = True,
) -> ApplyResult:
    """Translate plan into rope operations executed transactionally per-change.

    Each project.do() call is a separate history entry so that rollback()
    can undo them one at a time.

    Note: LibCST byte offsets for symbol moves are computed AFTER file moves
    because rope rewrites imports (changing file content) when files are
    relocated, invalidating pre-computed offsets.
    """
    applied: list[AppliedAction] = []
    escalations: list[Escalation] = []

    symbol_moves_to_apply = [
        sm for sm in plan.symbol_moves
        if (not only_approved_symbols or sm.approved)
    ]

    # ------------------------------------------------------------------
    # LibCST pre-flight for scope safety (using pre-move content).
    # Offsets will be recomputed after file moves to get current content.
    # ------------------------------------------------------------------
    for sm in symbol_moves_to_apply:
        resolved = _resolve_src_path(repo_root, sm.src_file)
        if resolved is None:
            continue
        # Run scope analysis to flag unresolvable accesses (string-form refs)
        _, pre_escalations = _preflight_file(resolved, [(sm.symbol_id, sm.label)])
        escalations.extend(pre_escalations)

    # ------------------------------------------------------------------
    # rope apply
    # ------------------------------------------------------------------
    project = Project(str(repo_root))
    # Track __init__.py files we create so rollback can clean them up
    created_init_files: list[Path] = []
    try:
        affected_resources: list = []  # collect for organize_imports pass

        # --- file moves ---
        for fm in plan.file_moves:
            resolved_src = _resolve_src_path(repo_root, fm.src)
            if resolved_src is None:
                log.warning("Skipping file move: cannot resolve %s under %s", fm.src, repo_root)
                continue

            # Ensure dest package dir exists with __init__.py
            dest_pkg_path = repo_root / fm.cluster
            dest_pkg_path.mkdir(parents=True, exist_ok=True)
            init_file = dest_pkg_path / "__init__.py"
            if not init_file.exists():
                init_file.touch()
                created_init_files.append(init_file)

            try:
                src_resource = libutils.path_to_resource(project, str(resolved_src))
                dest_resource = libutils.path_to_resource(
                    project, str(dest_pkg_path), type="folder"
                )
                mover = MoveModule(project, src_resource)
                changes = mover.get_changes(dest_resource)
                project.do(changes)
                history_index = len(project.history.undo_list)
                applied.append(
                    AppliedAction(
                        kind="file_move",
                        description=f"Moved {fm.src} → {fm.dest}",
                        history_index=history_index,
                    )
                )
                # Record dest resource for organize_imports
                dest_file = dest_pkg_path / Path(fm.src).name
                if dest_file.exists():
                    try:
                        dest_res = libutils.path_to_resource(project, str(dest_file))
                        affected_resources.append(dest_res)
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("file_move failed for %s: %s", fm.src, exc)
                escalations.append(
                    Escalation(
                        kind="move_error",
                        symbol_id=fm.src,
                        detail=str(exc),
                    )
                )

        # --- symbol moves ---
        # Offsets computed NOW, after file moves, from current file content.
        for sm in symbol_moves_to_apply:
            # Find file at its *current* location (may have been relocated by rope)
            current_src = _find_current_path(repo_root, sm.src_file)
            if current_src is None:
                escalations.append(
                    Escalation(
                        kind="no_referent",
                        symbol_id=sm.symbol_id,
                        detail=f"Source file '{sm.src_file}' not found after file moves",
                    )
                )
                continue

            # Compute offset from current content
            off_map, pre_esc = _preflight_file(current_src, [(sm.symbol_id, sm.label)])
            escalations.extend(pre_esc)
            key = (str(current_src), sm.label)
            offset = off_map.get(key)
            if offset is None:
                continue  # escalation already recorded in _preflight_file

            # Destination module for moveglobal
            dest_cluster_path = repo_root / sm.dest_cluster
            if not dest_cluster_path.exists():
                dest_cluster_path.mkdir(parents=True, exist_ok=True)
                init = dest_cluster_path / "__init__.py"
                init.touch()
                created_init_files.append(init)
            dest_module_path = dest_cluster_path / "_unsorted.py"
            if not dest_module_path.exists():
                dest_module_path.write_text("")

            try:
                src_resource = libutils.path_to_resource(project, str(current_src))
                dest_resource = libutils.path_to_resource(project, str(dest_module_path))
                mover = create_move(project, src_resource, offset)
                changes = mover.get_changes(dest_resource)
                project.do(changes)
                history_index = len(project.history.undo_list)
                applied.append(
                    AppliedAction(
                        kind="symbol_move",
                        description=(
                            f"Moved symbol {sm.label} from {sm.src_file} "
                            f"→ {sm.dest_cluster}/_unsorted.py"
                        ),
                        history_index=history_index,
                    )
                )
                if dest_module_path.exists():
                    try:
                        dest_res = libutils.path_to_resource(project, str(dest_module_path))
                        affected_resources.append(dest_res)
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("symbol_move failed for %s: %s", sm.label, exc)
                escalations.append(
                    Escalation(
                        kind="move_error",
                        symbol_id=sm.symbol_id,
                        detail=str(exc),
                    )
                )

        # --- organize_imports pass for affected files ---
        organizer = ImportOrganizer(project)
        seen_paths: set[str] = set()
        for resource in affected_resources:
            try:
                if resource.path in seen_paths:
                    continue
                seen_paths.add(resource.path)
                if not Path(project.address).joinpath(resource.path).exists():
                    continue
                changes = organizer.organize_imports(resource)
                if changes is not None:
                    project.do(changes)
                    history_index = len(project.history.undo_list)
                    applied.append(
                        AppliedAction(
                            kind="organize_imports",
                            description=f"Organized imports in {resource.path}",
                            history_index=history_index,
                        )
                    )
            except Exception as exc:
                log.debug("organize_imports skipped for %s: %s", resource.path, exc)

    finally:
        project.close()

    # Persist the list of __init__.py files we created so rollback() can remove them.
    _write_init_manifest(repo_root, created_init_files)

    return ApplyResult(applied=applied, escalations=escalations)


# ---------------------------------------------------------------------------
# Manifest helpers for rollback
# ---------------------------------------------------------------------------

_MANIFEST_FILE = ".ropeproject/refactor_created_inits.json"


def _write_init_manifest(repo_root: Path, created: list[Path]) -> None:
    """Persist paths of __init__.py files created during apply_plan."""
    import json
    manifest_path = repo_root / _MANIFEST_FILE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps([str(p.relative_to(repo_root)) for p in created])
    )


def _read_init_manifest(repo_root: Path) -> list[Path]:
    """Return paths from the manifest (absolute)."""
    import json
    manifest_path = repo_root / _MANIFEST_FILE
    if not manifest_path.exists():
        return []
    try:
        data = json.loads(manifest_path.read_text())
        return [repo_root / p for p in data]
    except Exception:
        return []


def rollback(repo_root: Path, applied_count: int) -> None:
    """Open the rope project and undo applied_count history entries.

    Also removes __init__.py files that apply_plan created (rope's history
    does not track pathlib file creations).
    """
    project = Project(str(repo_root))
    try:
        for _ in range(applied_count):
            if project.history.undo_list:
                project.history.undo()
    finally:
        project.close()

    # Remove __init__.py files we created (rope doesn't track them)
    created_inits = _read_init_manifest(repo_root)
    for init_path in created_inits:
        if init_path.exists():
            try:
                init_path.unlink()
                # Remove parent dir if now empty
                parent = init_path.parent
                if parent != repo_root and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception as exc:
                log.debug("Could not remove %s during rollback: %s", init_path, exc)

    # Clean up manifest
    manifest_path = repo_root / _MANIFEST_FILE
    if manifest_path.exists():
        manifest_path.unlink()
