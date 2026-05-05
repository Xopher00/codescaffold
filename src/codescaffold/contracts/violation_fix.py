"""Propose alternative move targets for moves that violate import contracts."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import networkx as nx

from codescaffold.candidates.models import MoveCandidate
from codescaffold.graphify.snapshot import GraphSnapshot
from codescaffold.plans.schema import ApprovedMove

from .package_graph import _file_to_subpackage, build_package_dag


def propose_alternatives(
    failed_moves: tuple[ApprovedMove, ...],
    snap: GraphSnapshot,
    layers: tuple[tuple[str, ...], ...],
) -> list[MoveCandidate]:
    """For each move that introduced a violation, find alternative target packages.

    Strategy: for the symbol's source package, determine its layer index.
    Legal targets are packages at the *same or lower* layer (we can't import
    upward without violating the contract). Among legal targets, pick the one
    with the strongest graph connection to the symbol's neighbors.

    Returns MoveCandidate instances the user can re-approve.
    """
    G = snap.graph
    node_to_pkg: dict[str, str] = {}
    for node in G.nodes():
        src = G.nodes[node].get("source_file", "")
        pkg = _file_to_subpackage(src)
        if pkg:
            node_to_pkg[node] = pkg

    # Build layer index: pkg → int (0 = top/most upstream)
    pkg_to_layer: dict[str, int] = {}
    for i, layer in enumerate(layers):
        for pkg in layer:
            pkg_to_layer[pkg] = i

    candidates: list[MoveCandidate] = []

    for move in failed_moves:
        source_pkg = _file_to_subpackage(move.source_file) or ""
        target_pkg = _file_to_subpackage(move.target_file) or ""

        source_layer = pkg_to_layer.get(source_pkg, -1)
        target_layer = pkg_to_layer.get(target_pkg, -1)

        if source_layer < 0 or target_layer < 0:
            continue  # not in the layer map — can't propose

        # Legal target layers: anything at >= source_layer (same level or deeper/upstream)
        legal_pkgs = [
            pkg for pkg, li in pkg_to_layer.items()
            if li >= source_layer and pkg != source_pkg
        ]
        if not legal_pkgs:
            continue

        # Find nodes with the same symbol name in the snapshot
        if move.symbol:
            node = _find_node_by_label(G, move.symbol)
        else:
            node = _find_node_by_file(G, move.source_file)

        if node is None:
            continue

        # Score legal packages by neighbor count
        neighbor_pkgs = [
            node_to_pkg.get(nb) for nb in G.neighbors(node) if node_to_pkg.get(nb)
        ]
        pkg_scores = Counter(p for p in neighbor_pkgs if p in legal_pkgs)
        if not pkg_scores:
            # Fall back: just pick the highest-layer legal package
            best_pkg = min(legal_pkgs, key=lambda p: pkg_to_layer[p])
        else:
            best_pkg, _ = pkg_scores.most_common(1)[0]

        target_file = _dominant_file_for_pkg(G, node_to_pkg, best_pkg)
        if not target_file or target_file == move.source_file:
            continue

        candidates.append(MoveCandidate(
            kind=move.kind,
            source_file=move.source_file,
            symbol=move.symbol,
            target_file=target_file,
            community_id=-1,
            reasons=(
                f"original target `{move.target_file}` is in layer {target_layer} "
                f"(above source layer {source_layer}), which would violate the contract",
            ),
            confidence="low",
        ))

    return candidates


def _find_node_by_label(G: nx.Graph, label: str) -> str | None:
    for node in G.nodes():
        if G.nodes[node].get("label") == label:
            return node
    return None


def _find_node_by_file(G: nx.Graph, source_file: str) -> str | None:
    for node in G.nodes():
        if G.nodes[node].get("source_file") == source_file:
            return node
    return None


def _dominant_file_for_pkg(
    G: nx.Graph, node_to_pkg: dict[str, str], target_pkg: str
) -> str | None:
    files = [
        G.nodes[n].get("source_file", "")
        for n in G.nodes()
        if node_to_pkg.get(n) == target_pkg and G.nodes[n].get("source_file")
    ]
    if not files:
        return None
    return Counter(files).most_common(1)[0][0]
