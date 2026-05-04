from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from .graph_bridge import load_graph

logger = logging.getLogger(__name__)


@dataclass
class ClusterView:
    file_communities: dict[int, list[str]]
    G: nx.Graph
    # Raw symbol-level community assignments from graphify: {community_id: [node_ids]}
    symbol_communities: dict[int, list[str]] = field(default_factory=dict)
    # Populated by build_view() via graphify.cluster / graphify.analyze
    cohesion: dict[int, float] = field(default_factory=dict)
    god_nodes: list[dict] = field(default_factory=list)
    surprising_connections: list[dict] = field(default_factory=list)
    # Node IDs (not labels) for structural role detection
    bridge_nodes: set[str] = field(default_factory=set)   # high betweenness, graphify-filtered
    isolated_nodes: set[str] = field(default_factory=set) # degree ≤ 1, graphify-filtered


def build_view(graph_json: Path) -> ClusterView:
    G, communities = load_graph(graph_json)

    file_communities: dict[int, list[str]] = {}
    for comm_id, node_ids in communities.items():
        files: set[str] = set()
        for nid in node_ids:
            attrs = G.nodes.get(nid, {})
            sf = attrs.get("source_file")
            if sf:
                files.add(sf)
        if files:
            file_communities[int(comm_id)] = sorted(files)

    cohesion: dict[int, float] = {}
    gods: list[dict] = []
    surprises: list[dict] = []
    bridge_nodes: set[str] = set()
    isolated_nodes: set[str] = set()
    try:
        from graphify.cluster import score_all
        from graphify.analyze import god_nodes, surprising_connections, _is_file_node, _is_concept_node

        cohesion = score_all(G, communities)

        raw_gods = god_nodes(G)
        # Enrich each god node with its source_file from the graph
        gods = [
            {**g, "source_file": G.nodes.get(g.get("id", ""), {}).get("source_file", "")}
            for g in raw_gods
        ]

        raw_surprises = surprising_connections(G, communities)
        # Build label → file_type index so we can drop rationale nodes
        label_to_type: dict[str, str] = {
            attrs.get("label", ""): attrs.get("file_type", "code")
            for _, attrs in G.nodes(data=True)
            if attrs.get("label")
        }
        surprises = [
            s for s in raw_surprises
            if label_to_type.get(s.get("source", ""), "code") != "rationale"
            and label_to_type.get(s.get("target", ""), "code") != "rationale"
        ]

        # Bridge nodes: same filter as graphify.analyze.suggest_questions uses.
        if G.number_of_edges() > 0:
            k = min(100, G.number_of_nodes()) if G.number_of_nodes() > 1000 else None
            betweenness = nx.betweenness_centrality(G, k=k)
            bridge_nodes = {
                n for n, score in betweenness.items()
                if score > 0 and not _is_file_node(G, n) and not _is_concept_node(G, n)
            }

        # Isolated nodes: same filter as graphify.analyze.suggest_questions uses.
        isolated_nodes = {
            n for n in G.nodes()
            if G.degree(n) <= 1 and not _is_file_node(G, n) and not _is_concept_node(G, n)
        }

    except Exception as exc:
        logger.warning("graphify analysis unavailable: %s", exc)

    return ClusterView(
        file_communities=file_communities,
        symbol_communities=communities,
        G=G,
        cohesion=cohesion,
        god_nodes=gods,
        surprising_connections=surprises,
        bridge_nodes=bridge_nodes,
        isolated_nodes=isolated_nodes,
    )
