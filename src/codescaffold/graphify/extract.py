"""run_extract: collect files, run graphify AST extraction, return a GraphSnapshot."""

from __future__ import annotations

from pathlib import Path
import networkx as nx

from .vendor import (
    build_from_json,
    collect_files,
    _extract,
    cached_files,
    check_semantic_cache,
    save_semantic_cache,
)
from .snapshot import GraphSnapshot


def run_extract(repo_path: Path, *, directed: bool = True) -> GraphSnapshot:
    """Extract a graph snapshot from a repository directory.

    Runs graphify's AST extractor on all supported files under repo_path,
    builds a directed NetworkX graph, detects communities, and records
    a sha256 hash of the graph structure for plan staleness detection.

    On every call, stale nodes and edges are pruned: files whose content has
    changed (AST hash cache miss) have their old semantic cache data discarded
    before the graph is built, and those files are re-extracted fresh.

    directed=False falls back to an undirected graph (legacy behaviour).
    """
    repo_path = Path(repo_path).resolve()
    files = collect_files(repo_path)
    if not files:
        return GraphSnapshot.from_graph(nx.DiGraph() if directed else nx.Graph())

    file_strs = [str(f) for f in files]

    # Files whose content hash still matches the AST cache — unchanged since last run.
    valid_ast = cached_files(repo_path)

    # Split semantic cache: cached (nodes/edges) vs files never seen before.
    sem_nodes, sem_edges, sem_hyper, sem_uncached = check_semantic_cache(
        file_strs, root=repo_path
    )

    # Stale = in semantic cache but content has changed (AST hash invalid).
    # Their old nodes/edges must be pruned to avoid ghost data in the graph.
    sem_uncached_set = set(sem_uncached)
    stale = {f for f in file_strs if f not in valid_ast and f not in sem_uncached_set}

    if stale:
        sem_nodes = [n for n in sem_nodes if n.get("source_file") not in stale]
        sem_edges = [e for e in sem_edges if e.get("source_file") not in stale]

    # Re-extract stale and never-seen files.
    needs_fresh = [Path(f) for f in sem_uncached_set | stale]
    if needs_fresh:
        fresh = _extract(needs_fresh, cache_root=repo_path)
        fresh_nodes = fresh.get("nodes", [])
        fresh_edges = fresh.get("edges", [])
        save_semantic_cache(fresh_nodes, fresh_edges, root=repo_path)
    else:
        fresh_nodes, fresh_edges = [], []

    all_nodes = sem_nodes + fresh_nodes
    all_edges = sem_edges + fresh_edges

    # build_from_json only adds edges whose both endpoints are in the nodes list.
    # Package-reference nodes (e.g. 'codescaffold_sandbox') are created by graphify
    # as edge targets for cross-package imports but have no source_file and are
    # never persisted as standalone nodes by save_semantic_cache. Add bare stubs
    # so these edges survive into the graph.
    known_ids = {n["id"] for n in all_nodes}
    stubs = []
    for edge in all_edges:
        for key in ("source", "target"):
            ref = edge.get(key)
            if ref and ref not in known_ids:
                stubs.append({"id": ref, "label": ref})
                known_ids.add(ref)

    extraction = {"nodes": all_nodes + stubs, "edges": all_edges}
    G = build_from_json(extraction, directed=directed)
    return GraphSnapshot.from_graph(G)
