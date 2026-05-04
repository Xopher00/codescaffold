from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel

from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.layout import detect_layout, _is_test_file

logger = logging.getLogger(__name__)


class FileMoveProposal(BaseModel):
    source: str
    dest: str
    dest_package: str


class SymbolMoveProposal(BaseModel):
    source: str
    dest: str
    symbol: str
    approved: bool = False


class ClusterInfo(BaseModel):
    community_id: int
    source_files: list[str]
    proposed_package: str | None = None
    cohesion: float | None = None
    risk_level: str | None = None  # LOW / MEDIUM / HIGH based on cohesion score


class PendingDecision(BaseModel):
    community_id: int
    source_files: list[str]
    current_dirs: dict[str, list[str]]  # dir_path → [file_paths]
    needs_placement: bool               # True when files span multiple directories
    cohesion: float | None
    risk_level: str
    cross_cluster_edges: list[dict]     # top edges leaving this community
    surprising_connections: list[dict]  # surprising_connections entries for files here


class RefactorPlan(BaseModel):
    file_moves: list[FileMoveProposal] = []       # populated by approve_moves, not plan()
    symbol_moves: list[SymbolMoveProposal] = []
    clusters: list[ClusterInfo] = []
    pending_decisions: list[PendingDecision] = []
    source_root: str | None = None
    validation_commands: list[str] = [
        "python -m compileall .",
        "pytest -q",
    ]


def _risk_level(cohesion: float | None) -> str:
    if cohesion is None:
        return "LOW"
    if cohesion < 0.1:
        return "HIGH"
    if cohesion < 0.3:
        return "MEDIUM"
    return "LOW"


def _cross_cluster_edges(view: ClusterView, community_files: set[str]) -> list[dict]:
    """Return up to 10 outgoing edges from community_files to other nodes, by weight."""
    edges: list[dict] = []
    for src, tgt, data in view.G.edges(data=True):
        src_file = view.G.nodes.get(src, {}).get("source_file", "")
        tgt_file = view.G.nodes.get(tgt, {}).get("source_file", "")
        if src_file in community_files and tgt_file not in community_files:
            edges.append({
                "source": src,
                "target": tgt,
                "source_file": src_file,
                "target_file": tgt_file,
                "relation": data.get("relation", ""),
                "weight": data.get("weight", 1.0),
            })
    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges[:10]


def plan(view: ClusterView, repo_root: Path, graph_json: Path) -> RefactorPlan:
    all_files = [sf for files in view.file_communities.values() for sf in files]
    layout = detect_layout(repo_root, all_files)
    src_root = layout.source_root

    logger.debug("source_root=%s", src_root)

    # Assign each file to its lowest community id to avoid contradictory proposals.
    file_to_community: dict[str, int] = {}
    community_resolved: dict[int, list[Path]] = {}

    for comm_id in sorted(view.file_communities):
        resolved: list[Path] = []
        for sf in view.file_communities[comm_id]:
            p = Path(sf)
            if not p.is_absolute():
                p = (repo_root / p).resolve()
            if _is_test_file(p) or p.name == "__init__.py":
                continue
            sf_key = str(p)
            if sf_key not in file_to_community:
                file_to_community[sf_key] = comm_id
                resolved.append(p)
        community_resolved[comm_id] = resolved

    # Build a set of source_files per community for edge lookups.
    comm_file_sets: dict[int, set[str]] = {
        cid: {str(p) for p in ps} for cid, ps in community_resolved.items()
    }

    # Surprising connections indexed by source_file for fast lookup.
    surprise_by_file: dict[str, list[dict]] = {}
    for s in view.surprising_connections:
        key = s.get("source_file", s.get("source", ""))
        surprise_by_file.setdefault(key, []).append(s)

    clusters: list[ClusterInfo] = []
    pending_decisions: list[PendingDecision] = []

    for comm_id in sorted(view.file_communities):
        resolved = community_resolved[comm_id]
        if not resolved:
            continue

        coh = view.cohesion.get(comm_id) if view.cohesion else None
        risk = _risk_level(coh)

        clusters.append(ClusterInfo(
            community_id=comm_id,
            source_files=[str(p) for p in resolved],
            proposed_package=None,
            cohesion=coh,
            risk_level=risk,
        ))

        if len(resolved) == 1:
            continue

        # Group by parent directory.
        current_dirs: dict[str, list[str]] = {}
        for p in resolved:
            current_dirs.setdefault(str(p.parent), []).append(str(p))

        community_file_strs = comm_file_sets[comm_id]

        # Surprising connections for files in this community.
        surprises: list[dict] = []
        for f in community_file_strs:
            surprises.extend(surprise_by_file.get(f, []))

        pending_decisions.append(PendingDecision(
            community_id=comm_id,
            source_files=[str(p) for p in resolved],
            current_dirs=current_dirs,
            needs_placement=len(current_dirs) > 1,
            cohesion=coh,
            risk_level=risk,
            cross_cluster_edges=_cross_cluster_edges(view, community_file_strs),
            surprising_connections=surprises,
        ))

    return RefactorPlan(
        file_moves=[],
        clusters=clusters,
        pending_decisions=pending_decisions,
        source_root=str(src_root),
    )


def write_plan(refactor_plan: RefactorPlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(refactor_plan.model_dump_json(indent=2), encoding="utf-8")
    return path
