from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from .graph_bridge import load_graph


@dataclass
class ClusterView:
    file_communities: dict[int, list[str]]
    G: nx.Graph


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

    return ClusterView(file_communities=file_communities, G=G)
