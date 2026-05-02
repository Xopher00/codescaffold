"""Cluster view: file-level community projection + graphify passthroughs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import graphify.analyze as ganalyze
import graphify.build as gbuild
import graphify.cluster as gcluster
import networkx as nx
from pydantic import BaseModel


class FileCluster(BaseModel):
    id: int
    files: list[str]
    cohesion: float


class MisplacedSymbol(BaseModel):
    symbol_id: str
    label: str
    host_file: str
    host_community: int
    target_community: int


class GraphView(BaseModel):
    file_clusters: list[FileCluster]
    misplaced_symbols: list[MisplacedSymbol]
    god_nodes: list[dict]
    surprising_connections: list[dict]
    suggested_questions: list[dict]
    community_cohesion: dict[int, float]


def load_graph(graph_json_path: Path) -> nx.Graph:
    data = json.loads(graph_json_path.read_text())
    try:
        return gbuild.build_from_json(data)
    except Exception:
        from networkx.readwrite import json_graph
        return json_graph.node_link_graph(data, edges="links")


def build_view(graph_json_path: Path) -> GraphView:
    G = load_graph(graph_json_path)

    # Step 1: recover communities from per-node attribute.
    communities: dict[int, list[str]] = {}
    for n, d in G.nodes(data=True):
        cid = d.get("community")
        if cid is not None:
            communities.setdefault(cid, []).append(n)

    # Step 2: project to file level.
    # For each source_file, vote over all non-rationale nodes (file nodes included
    # in the vote so that files with no symbol nodes still resolve, and so that
    # god modules — where symbols split equally — resolve to the file node's own
    # community rather than an arbitrary symbol community).
    file_votes: dict[str, Counter[int]] = {}
    for n, d in G.nodes(data=True):
        sf = d.get("source_file", "")
        if not sf:
            continue
        if "rationale" in n:
            continue
        cid = d.get("community")
        if cid is not None:
            file_votes.setdefault(sf, Counter())[cid] += 1

    projected_communities: dict[str, int] = {}
    for sf, counts in file_votes.items():
        max_count = max(counts.values())
        projected_communities[sf] = min(
            cid for cid, c in counts.items() if c == max_count
        )

    # Step 3: group files by projected community.
    cluster_files: dict[int, list[str]] = {}
    for sf, cid in projected_communities.items():
        cluster_files.setdefault(cid, []).append(sf)

    # Step 4: passthroughs.
    community_cohesion: dict[int, float] = gcluster.score_all(G, communities)
    god_nodes: list[dict] = ganalyze.god_nodes(G, top_n=10)
    surprising_connections: list[dict] = ganalyze.surprising_connections(
        G, communities, top_n=20
    )
    labels = {cid: f"pkg_{cid:03d}" for cid in communities}
    suggested_questions: list[dict] = ganalyze.suggest_questions(
        G, communities, labels
    )

    # Step 5: build FileCluster list (sorted by id ascending).
    file_clusters = [
        FileCluster(
            id=cid,
            files=sorted(cluster_files[cid]),
            cohesion=community_cohesion.get(cid, 0.0),
        )
        for cid in sorted(cluster_files)
    ]

    # Step 6: misplaced symbols — binary, no scoring.
    misplaced: list[MisplacedSymbol] = []
    for n, d in G.nodes(data=True):
        if "rationale" in n:
            continue
        sf = d.get("source_file", "")
        if not sf:
            continue
        if d.get("label") == Path(sf).name:
            continue  # skip file nodes
        host_c = projected_communities.get(sf)
        node_c = d.get("community")
        if node_c is None or host_c is None or node_c == host_c:
            continue
        misplaced.append(
            MisplacedSymbol(
                symbol_id=n,
                label=d.get("label", n),
                host_file=sf,
                host_community=host_c,
                target_community=node_c,
            )
        )
    misplaced.sort(key=lambda m: (m.host_file, m.label))

    return GraphView(
        file_clusters=file_clusters,
        misplaced_symbols=misplaced,
        god_nodes=god_nodes,
        surprising_connections=surprising_connections,
        suggested_questions=suggested_questions,
        community_cohesion=community_cohesion,
    )
