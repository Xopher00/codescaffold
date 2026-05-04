from __future__ import annotations

import logging
from pathlib import Path

from refactor_plan.execution.models import ClusterInfo
from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.layout import detect_layout, _is_test_file
from refactor_plan.planning.models import RefactorPlan, PendingDecision, SymbolMoveProposal


logger = logging.getLogger(__name__)


def _risk_level(cohesion: float | None) -> str:
    if cohesion is None:
        return "LOW"
    if cohesion < 0.1:
        return "HIGH"
    if cohesion < 0.3:
        return "MEDIUM"
    return "LOW"


def _norm_path(sf: str, repo_root: Path) -> str:
    """Normalize a graphify source_file value to an absolute path string."""
    if not sf:
        return sf
    p = Path(sf)
    return str(p) if p.is_absolute() else str((repo_root / p).resolve())


def _cross_cluster_edges(
    view: ClusterView, community_files: set[str], repo_root: Path
) -> list[dict]:
    """Return up to 20 cross-cluster edges (outgoing and incoming), by weight.

    community_files contains absolute paths. Graphify nodes may store relative
    paths, so each source_file is normalized before membership tests.
    """
    edges: list[dict] = []
    for src, tgt, data in view.G.edges(data=True):
        raw_sf = view.G.nodes.get(src, {}).get("source_file", "")
        raw_tf = view.G.nodes.get(tgt, {}).get("source_file", "")
        src_file = _norm_path(raw_sf, repo_root)
        tgt_file = _norm_path(raw_tf, repo_root)
        src_in = src_file in community_files
        tgt_in = tgt_file in community_files
        if src_in == tgt_in:
            continue
        edges.append({
            "source": src,
            "target": tgt,
            "source_file": src_file,
            "target_file": tgt_file,
            "relation": data.get("relation", ""),
            "weight": data.get("weight", 1.0),
            "direction": "outgoing" if src_in else "incoming",
        })
    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges[:20]


def _best_dest_file(
    G: object,
    node_id: str,
    target_files: list[str],
    file_to_nodes: dict[str, list[str]],
) -> str | None:
    """Target file in the community with the most edges to node_id."""
    import networkx as nx
    assert isinstance(G, nx.Graph)
    neighbors = set(G.neighbors(node_id))
    best_file, best_count = None, 0
    for f in target_files:
        count = len(neighbors & set(file_to_nodes.get(f, [])))
        if count > best_count:
            best_count, best_file = count, f
    return best_file


def _symbol_move_proposals(
    view: ClusterView,
    file_to_community: dict[str, int],
    community_to_files: dict[int, list[str]],
    repo_root: Path,
) -> list[SymbolMoveProposal]:
    """Find symbols whose graphify community differs from their file's community.

    These are candidates for symbol extraction: the symbol is structurally
    closer to another community than the file it currently lives in.
    Only considers real symbols (degree ≥ 2, not file-hub stubs).
    """
    if not view.symbol_communities:
        return []

    node_to_community: dict[str, int] = {
        nid: comm_id
        for comm_id, node_ids in view.symbol_communities.items()
        for nid in node_ids
    }

    file_to_nodes: dict[str, list[str]] = {}
    for nid, attrs in view.G.nodes(data=True):
        sf = _norm_path(attrs.get("source_file", ""), repo_root)
        if sf:
            file_to_nodes.setdefault(sf, []).append(nid)

    proposals: list[SymbolMoveProposal] = []
    seen: set[tuple[str, str]] = set()  # (source_file, symbol) dedup

    for file_path, file_comm_id in file_to_community.items():
        for nid in file_to_nodes.get(file_path, []):
            attrs = view.G.nodes.get(nid, {})
            label = attrs.get("label", "")
            if not label:
                continue
            # Skip rationale/docstring nodes (graphify injects these as edge labels)
            if attrs.get("file_type") == "rationale":
                continue
            # Skip labels that are clearly docstrings, not symbol names (contain spaces)
            if " " in label:
                continue
            # Skip file-level hub stubs (graphify synthetic nodes)
            if label == Path(file_path).name:
                continue
            if label.startswith(".") and label.endswith("()"):
                continue
            # Only move symbols with meaningful connectivity
            if view.G.degree(nid) < 2:
                continue

            symbol_comm = node_to_community.get(nid)
            if symbol_comm is None or symbol_comm == file_comm_id:
                continue

            target_files = community_to_files.get(symbol_comm, [])
            if not target_files:
                continue

            dest = _best_dest_file(view.G, nid, target_files, file_to_nodes) or target_files[0]
            key = (file_path, label)
            if key not in seen:
                seen.add(key)
                proposals.append(SymbolMoveProposal(source=file_path, dest=dest, symbol=label))

    return proposals


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
            cross_cluster_edges=_cross_cluster_edges(view, community_file_strs, repo_root),
            surprising_connections=surprises,
        ))

    community_to_files: dict[int, list[str]] = {
        cid: [str(p) for p in ps] for cid, ps in community_resolved.items()
    }
    symbol_moves = _symbol_move_proposals(view, file_to_community, community_to_files, repo_root)

    return RefactorPlan(
        file_moves=[],
        symbol_moves=symbol_moves,
        clusters=clusters,
        pending_decisions=pending_decisions,
        source_root=str(src_root),
    )


def write_plan(refactor_plan: RefactorPlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(refactor_plan.model_dump_json(indent=2), encoding="utf-8")
    return path
