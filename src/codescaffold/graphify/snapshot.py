"""GraphSnapshot: a graph + community partition + hash for staleness detection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import networkx as nx
from .vendor import cluster, cohesion_score, score_all


@dataclass(frozen=True)
class GraphSnapshot:
    """Immutable graph + community state captured at analysis time.

    graph_hash encodes the node/edge structure so plans can detect when the
    repo has changed since analysis was run (staleness check).
    """

    graph: nx.Graph
    communities: dict[int, list[str]]
    graph_hash: str

    @classmethod
    def from_graph(cls, G: nx.Graph) -> "GraphSnapshot":
        comms = cluster(G)
        h = _hash_graph(G)
        return cls(graph=G, communities=comms, graph_hash=h)

    def cohesion_scores(self) -> dict[int, float]:
        return score_all(self.graph, self.communities)

    def community_cohesion(self, community_id: int) -> float:
        nodes = self.communities.get(community_id, [])
        return cohesion_score(self.graph, nodes)


def _hash_graph(G: nx.Graph) -> str:
    """Stable sha256 over the sorted node and edge structure."""
    nodes = sorted(G.nodes())
    if G.is_directed():
        edges = sorted((u, v) for u, v in G.edges())
    else:
        edges = sorted((min(u, v), max(u, v)) for u, v in G.edges())
    payload = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
