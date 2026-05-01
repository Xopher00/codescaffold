"""E3 — God-module splitter.

Triggered by graphify's `suggest_questions[type ∈ {low_cohesion, bridge_node}]`.
Re-derives the actual symbol set per trigger, allocates fresh placeholder
mod_NNN.py destinations within the existing target package, and applies via
sequenced rope MoveGlobal.

Composes Part A signals only:
- graphify.cluster.cohesion_score with the threshold from graphify
  analyze.py:436 (`< 0.15 and len(nodes) >= 5`).
- nx.betweenness_centrality with the k/seed parameters from graphify
  analyze.py:365 (`k = min(100, N) if N > 1000 else None, seed=42`).
- AMBIGUOUS edges skipped per cardinal rule.
"""

from __future__ import annotations

import logging
from pathlib import Path

import graphify.cluster as gcluster
import networkx as nx
from graphify.analyze import _is_concept_node, _is_file_node
from pydantic import BaseModel
from rope.base import libutils
from rope.base.project import Project
from rope.refactor.move import MoveGlobal, create_move

from refactor_plan.applicator.rope_runner import (
    AppliedAction,
    ApplyResult,
    Escalation,
    _ensure_future_annotations,
    _is_residue,
    _pre_create_dest_module,
    _preflight_file,
    _rewrite_cross_cluster_imports,
    _write_stray_deleted_manifest,
)
from refactor_plan.cluster_view import GraphView

log = logging.getLogger(__name__)


class SymbolSplit(BaseModel):
    symbol_id: str
    label: str
    source_file: str
    source_community: int
    target_community: int
    dest_pkg: str
    dest_mod: str
    rationale: str
    score: float
    approved: bool = False

    @property
    def dest_file(self) -> str:
        """Combined dest path expected by _rewrite_cross_cluster_imports (sm.dest_file)."""
        return f"{self.dest_pkg}/{self.dest_mod}"

    @property
    def src_file(self) -> str:
        """Alias expected by _rewrite_cross_cluster_imports (sm.src_file)."""
        return self.source_file


class SplitPlan(BaseModel):
    splits: list[SymbolSplit]
    triggers: list[dict]


def _has_only_ambiguous_evidence(G: nx.Graph, node_id: str) -> bool:
    """True iff every edge incident to node_id has confidence == AMBIGUOUS."""
    edges = list(G.edges(node_id, data=True))
    if not edges:
        return False
    return all(d.get("confidence") == "AMBIGUOUS" for _, _, d in edges)


def _next_mod_index(pkg_dir: Path) -> int:
    """Return next-available mod_NNN integer index in pkg_dir; 1 if none exist."""
    if not pkg_dir.exists():
        return 1
    indices: list[int] = []
    for p in pkg_dir.glob("mod_*.py"):
        try:
            indices.append(int(p.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return max(indices, default=0) + 1


def _pkg_name(community: int) -> str:
    """Match planner._allocate_pkg_names convention."""
    return f"pkg_{community:03d}"


def build_split_plan(view: GraphView, graph: nx.Graph, repo_root: Path) -> SplitPlan:
    """Build a SplitPlan from a GraphView.

    Triggers: presence of `low_cohesion` or `bridge_node` entries in
    `view.suggested_questions`. For each trigger type the actual node set is
    re-derived via the same primitives graphify uses (cohesion_score and
    betweenness_centrality), then filtered to symbols whose host-file
    community differs from the target — i.e. symbols that are structurally
    misplaced and should be relocated. AMBIGUOUS-edge-only nodes are
    excluded.
    """
    triggers = [
        q for q in view.suggested_questions
        if q.get("type") in ("low_cohesion", "bridge_node")
    ]
    if not triggers:
        return SplitPlan(splits=[], triggers=[])

    projected: dict[str, int] = {}
    for fc in view.file_clusters:
        for f in fc.files:
            projected[f] = fc.id

    communities: dict[int, list[str]] = {}
    for n, d in graph.nodes(data=True):
        cid = d.get("community")
        if cid is not None:
            communities.setdefault(cid, []).append(n)

    candidates: list[tuple[str, int, int, str, float]] = []

    if any(q.get("type") == "low_cohesion" for q in triggers):
        for cid, nodes in communities.items():
            if len(nodes) < 5:
                continue
            score = gcluster.cohesion_score(graph, nodes)
            if score >= 0.15:
                continue
            for node_id in nodes:
                if "rationale" in node_id:
                    continue
                if _is_file_node(graph, node_id) or _is_concept_node(graph, node_id):
                    continue
                sf = graph.nodes[node_id].get("source_file", "")
                host_c = projected.get(sf)
                if host_c is None or host_c == cid:
                    continue
                rationale = (
                    f"low_cohesion: community {cid} cohesion={score:.3f} "
                    f"(threshold < 0.15, size {len(nodes)})"
                )
                candidates.append((node_id, host_c, cid, rationale, score))

    if any(q.get("type") == "bridge_node" for q in triggers):
        n_nodes = graph.number_of_nodes()
        k = min(100, n_nodes) if n_nodes > 1000 else None
        if graph.number_of_edges() > 0:
            betweenness = nx.betweenness_centrality(graph, k=k, seed=42)
            ranked = sorted(
                (
                    (n, s) for n, s in betweenness.items()
                    if s > 0
                    and not _is_file_node(graph, n)
                    and not _is_concept_node(graph, n)
                ),
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            for node_id, score in ranked:
                node_c = graph.nodes[node_id].get("community")
                sf = graph.nodes[node_id].get("source_file", "")
                host_c = projected.get(sf)
                if node_c is None or host_c is None or host_c == node_c:
                    continue
                rationale = f"bridge_node: betweenness={score:.3f}"
                candidates.append((node_id, host_c, node_c, rationale, score))

    candidates = [c for c in candidates if not _has_only_ambiguous_evidence(graph, c[0])]

    seen: set[str] = set()
    deduped: list[tuple[str, int, int, str, float]] = []
    for c in candidates:
        if c[0] in seen:
            continue
        seen.add(c[0])
        deduped.append(c)

    deduped.sort(
        key=lambda c: (
            graph.nodes[c[0]].get("source_file", ""),
            graph.nodes[c[0]].get("label", c[0]),
        )
    )

    group_to_mod: dict[tuple[str, int], str] = {}
    next_indices: dict[int, int] = {}
    splits: list[SymbolSplit] = []
    for node_id, host_c, target_c, rationale, score in deduped:
        data = graph.nodes[node_id]
        sf = data.get("source_file", "")
        group_key = (sf, target_c)
        if group_key not in group_to_mod:
            if target_c not in next_indices:
                next_indices[target_c] = _next_mod_index(repo_root / _pkg_name(target_c))
            group_to_mod[group_key] = f"mod_{next_indices[target_c]:03d}.py"
            next_indices[target_c] += 1
        splits.append(
            SymbolSplit(
                symbol_id=node_id,
                label=data.get("label", node_id),
                source_file=sf,
                source_community=host_c,
                target_community=target_c,
                dest_pkg=_pkg_name(target_c),
                dest_mod=group_to_mod[group_key],
                rationale=rationale,
                score=score,
            )
        )

    return SplitPlan(splits=splits, triggers=triggers)


def apply_split_plan(
    plan: SplitPlan,
    repo_root: Path,
    *,
    only_approved: bool = True,
) -> ApplyResult:
    """Apply approved splits via sequenced rope MoveGlobal.

    Returns ApplyResult; the CLI layer (Wave C2) is responsible for calling
    `validator.validate(...)` afterward and triggering `rollback(...)` on
    non-zero validator exit.
    """
    applied: list[AppliedAction] = []
    escalations: list[Escalation] = []
    stray_deleted_files: dict[str, str] = {}

    splits = [s for s in plan.splits if (not only_approved or s.approved)]
    if not splits:
        return ApplyResult(applied=applied, escalations=escalations)

    for s in splits:
        src_path = repo_root / s.source_file
        if not src_path.exists():
            escalations.append(
                Escalation(
                    kind="no_referent",
                    symbol_id=s.symbol_id,
                    detail=f"Source file '{s.source_file}' not found at {src_path}",
                )
            )
            continue
        _, pre_esc = _preflight_file(src_path, [(s.symbol_id, s.label)])
        escalations.extend(pre_esc)

    project = Project(str(repo_root))
    try:
        seen_dests: set[Path] = set()
        for s in splits:
            dest_path = repo_root / s.dest_pkg / s.dest_mod
            if dest_path in seen_dests:
                continue
            seen_dests.add(dest_path)
            init = dest_path.parent / "__init__.py"
            if not init.exists():
                init.parent.mkdir(parents=True, exist_ok=True)
                init.touch()
            if _pre_create_dest_module(dest_path):
                applied.append(
                    AppliedAction(
                        kind="future_annotations_inject",
                        description=f"Injected `from __future__ import annotations` into {dest_path.relative_to(repo_root)}",
                        history_index=-1,
                    )
                )

        for s in splits:
            src_path = repo_root / s.source_file
            if not src_path.exists():
                continue
            off_map, pre_esc = _preflight_file(src_path, [(s.symbol_id, s.label)])
            escalations.extend(pre_esc)
            offset = off_map.get((str(src_path), s.label))
            if offset is None:
                continue

            dest_path = repo_root / s.dest_pkg / s.dest_mod
            try:
                src_resource = libutils.path_to_resource(project, str(src_path))
                assert src_resource is not None, f"resource not found: {src_path}"
                dest_resource = libutils.path_to_resource(project, str(dest_path))
                assert dest_resource is not None, f"resource not found: {dest_path}"
                mover = create_move(project, src_resource, offset)
                assert isinstance(mover, MoveGlobal)
                changes = mover.get_changes(dest_resource)
                project.do(changes)
                applied.append(
                    AppliedAction(
                        kind="symbol_move",
                        description=(
                            f"Split {s.label} from {s.source_file} → "
                            f"{s.dest_pkg}/{s.dest_mod}"
                        ),
                        history_index=len(project.history.undo_list),
                    )
                )
            except Exception as exc:
                escalations.append(
                    Escalation(
                        kind="move_error",
                        symbol_id=s.symbol_id,
                        detail=str(exc),
                    )
                )

        touched: set[Path] = set()
        for s in splits:
            src_path = repo_root / s.source_file
            if src_path.exists():
                touched.add(src_path)
            touched.add(repo_root / s.dest_pkg / s.dest_mod)
        for path in sorted(touched):
            if path.exists():
                _rewrite_cross_cluster_imports(path, repo_root, src_to_dest={}, symbol_moves=splits)

        residue_seen: set[str] = set()
        for s in splits:
            src_path = repo_root / s.source_file
            if not src_path.exists():
                continue
            key = str(src_path)
            if key in residue_seen:
                continue
            residue_seen.add(key)
            if _is_residue(src_path):
                rel_path = str(src_path.relative_to(repo_root))
                try:
                    stray_deleted_files[rel_path] = src_path.read_text(encoding="utf-8")
                except OSError:
                    stray_deleted_files[rel_path] = ""
                try:
                    src_path.unlink()
                    applied.append(
                        AppliedAction(
                            kind="residue_delete",
                            description=f"Deleted residue {rel_path}",
                            history_index=-1,
                        )
                    )
                except Exception as exc:
                    log.warning("Could not delete residue %s: %s", src_path, exc)
                    stray_deleted_files.pop(rel_path, None)
    finally:
        project.close()

    if stray_deleted_files:
        _write_stray_deleted_manifest(repo_root, stray_deleted_files)

    return ApplyResult(applied=applied, escalations=escalations)
