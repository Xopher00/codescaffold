"""run_extract: collect files, run graphify AST extraction, return a GraphSnapshot."""

from __future__ import annotations

from pathlib import Path
import networkx as nx

from .vendor import build_from_json, collect_files, _extract
from .snapshot import GraphSnapshot


def run_extract(repo_path: Path, *, directed: bool = True) -> GraphSnapshot:
    """Extract a graph snapshot from a repository directory.

    Runs graphify's AST extractor on all supported files under repo_path,
    builds a directed NetworkX graph, detects communities, and records
    a sha256 hash of the graph structure for plan staleness detection.

    directed=False falls back to an undirected graph (legacy behaviour).
    """
    

    repo_path = Path(repo_path).resolve()
    files = collect_files(repo_path)
    if not files:
        return GraphSnapshot.from_graph(nx.DiGraph() if directed else nx.Graph())
    extraction = _extract(files, cache_root=repo_path)
    G = build_from_json(extraction, directed=directed)
    return GraphSnapshot.from_graph(G)
