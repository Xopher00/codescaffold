"""Translate graph evidence into MoveCandidate proposals.

v1 heuristic:
  - Identify low-cohesion communities (score < LOW_COHESION_THRESHOLD).
  - Within each, find nodes where the plurality of neighbours belongs to a
    different community (the node is structurally "pulled" elsewhere).
  - Propose moving the node to the dominant source_file of the target community.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import networkx as nx

from codescaffold.graphify import GraphSnapshot

from .models import MoveCandidate

LOW_COHESION_THRESHOLD = 0.15
MIN_COMMUNITY_SIZE = 3      # skip trivially small communities
MIN_NEIGHBOR_RATIO = 0.5    # at least 50% of neighbors must point at target community


def propose_moves(snap: GraphSnapshot) -> list[MoveCandidate]:
    """Return a list of move candidates derived from graph community structure."""
    G = snap.graph
    communities = snap.communities
    scores = snap.cohesion_scores()

    node_to_community: dict[str, int] = {
        node: cid
        for cid, nodes in communities.items()
        for node in nodes
    }

    candidates: list[MoveCandidate] = []

    for cid, nodes in communities.items():
        if len(nodes) < MIN_COMMUNITY_SIZE:
            continue
        cohesion = scores.get(cid, 1.0)
        if cohesion >= LOW_COHESION_THRESHOLD:
            continue

        for node in nodes:
            source_file = G.nodes[node].get("source_file", "")
            label = G.nodes[node].get("label", node)
            if not source_file or not label:
                continue

            # Count which communities the node's neighbours live in
            neighbor_communities = [
                node_to_community[nb]
                for nb in G.neighbors(node)
                if nb in node_to_community and node_to_community[nb] != cid
            ]
            if not neighbor_communities:
                continue

            most_common_target, count = Counter(neighbor_communities).most_common(1)[0]
            total_neighbors = G.degree(node)
            if total_neighbors == 0:
                continue
            ratio = count / total_neighbors
            if ratio < MIN_NEIGHBOR_RATIO:
                continue

            target_file = _dominant_file(G, communities.get(most_common_target, []))
            if not target_file or target_file == source_file:
                continue

            confidence: str
            if ratio >= 0.8:
                confidence = "high"
            elif ratio >= 0.65:
                confidence = "medium"
            else:
                confidence = "low"

            reasons = (
                f"community {cid} has cohesion {cohesion:.2f} (below {LOW_COHESION_THRESHOLD})",
                f"{count}/{total_neighbors} neighbours are in community {most_common_target}",
            )

            candidates.append(
                MoveCandidate(
                    kind="symbol",
                    source_file=source_file,
                    symbol=label,
                    target_file=target_file,
                    community_id=cid,
                    reasons=reasons,
                    confidence=confidence,  # type: ignore[arg-type]
                )
            )

    # Deduplicate: same source_file + symbol may appear from multiple heuristic passes
    seen: set[tuple[str, str | None]] = set()
    unique: list[MoveCandidate] = []
    for c in candidates:
        key = (c.source_file, c.symbol)
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def _dominant_file(G: nx.Graph, nodes: list[str]) -> str | None:
    """Return the most common source_file among a list of nodes."""
    files = [G.nodes[n].get("source_file", "") for n in nodes if G.nodes[n].get("source_file")]
    if not files:
        return None
    return Counter(files).most_common(1)[0][0]
