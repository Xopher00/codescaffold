"""Cycle detection at both package and symbol granularity."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import grimp
import networkx as nx

from codescaffold.candidates.models import MoveCandidate
from codescaffold.graphify.snapshot import GraphSnapshot

from .models import CycleReport
from .package_graph import detect_root_package


def detect_package_cycles(
    repo_path: Path,
    snap: GraphSnapshot,
    src_root: str = "src",
) -> list[CycleReport]:
    """Detect package-level import cycles using grimp (excludes TYPE_CHECKING imports).

    Returns an empty list if the package graph is acyclic.
    """
    src = str(Path(repo_path) / src_root)
    if src not in sys.path:
        sys.path.insert(0, src)
    root_pkg = detect_root_package(Path(repo_path))
    g = grimp.build_graph(root_pkg, exclude_type_checking_imports=True)

    dag: nx.DiGraph = nx.DiGraph()
    for mod in g.modules:
        parts = mod.split(".")
        pkg = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
        dag.add_node(pkg)
        for upstream in g.find_modules_directly_imported_by(mod):
            up_parts = upstream.split(".")
            up_pkg = ".".join(up_parts[:2]) if len(up_parts) >= 2 else up_parts[0]
            if pkg != up_pkg:
                dag.add_edge(pkg, up_pkg)

    reports = []
    for cycle in nx.simple_cycles(dag):
        edges = tuple(zip(cycle, cycle[1:] + [cycle[0]]))
        suggested = _propose_cycle_break(cycle, snap, dag)
        reports.append(CycleReport(
            cycle=tuple(cycle),
            edges=edges,
            suggested_break=suggested,
        ))
    return reports


def _propose_cycle_break(
    pkg_cycle: list[str],
    snap: GraphSnapshot,
    dag: nx.DiGraph,
) -> MoveCandidate | None:
    """Find the best symbol to extract to break a package cycle."""
    G = snap.graph
    cycle_set = set(pkg_cycle)

    node_to_pkg: dict[str, str] = {}
    for node in G.nodes():
        src = G.nodes[node].get("source_file", "")
        from .package_graph import _file_to_subpackage
        pkg = _file_to_subpackage(src, "src")
        if pkg:
            node_to_pkg[node] = pkg

    best_node: str | None = None
    best_score = -1.0

    for node in G.nodes():
        node_pkg = node_to_pkg.get(node)
        if node_pkg not in cycle_set:
            continue
        neighbors = list(G.neighbors(node))
        if not neighbors:
            continue
        neighbor_pkgs = [node_to_pkg.get(nb) for nb in neighbors if node_to_pkg.get(nb)]
        external = [p for p in neighbor_pkgs if p in cycle_set and p != node_pkg]
        if not external:
            continue
        score = len(external) / len(neighbor_pkgs)
        if score > best_score:
            best_score = score
            best_node = node

    if best_node is None:
        return None

    source_file = G.nodes[best_node].get("source_file", "")
    label = G.nodes[best_node].get("label", best_node)

    neighbor_pkgs = [node_to_pkg.get(nb) for nb in G.neighbors(best_node) if node_to_pkg.get(nb)]
    external_pkgs = [p for p in neighbor_pkgs if p in cycle_set and p != node_to_pkg.get(best_node)]
    if not external_pkgs:
        return None
    target_pkg = Counter(external_pkgs).most_common(1)[0][0]

    target_files = [
        G.nodes[n].get("source_file", "")
        for n in G.nodes()
        if node_to_pkg.get(n) == target_pkg and G.nodes[n].get("source_file")
    ]
    if not target_files:
        return None
    target_file = Counter(target_files).most_common(1)[0][0]

    return MoveCandidate(
        kind="symbol",
        source_file=source_file,
        symbol=label,
        target_file=target_file,
        community_id=-1,
        reasons=(f"breaks cycle: {' → '.join(pkg_cycle + [pkg_cycle[0]])}",),
        confidence="medium",
    )
