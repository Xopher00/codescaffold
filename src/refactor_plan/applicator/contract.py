"""Generate .importlinter config from cluster topology.

Emits import-linter contracts (independence, layers, forbidden, acyclic_siblings)
to enforce architecture boundaries between pkg_NNN clusters.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from pydantic import BaseModel

from refactor_plan.cluster_view import GraphView, load_graph
from refactor_plan.planner import RefactorPlan


class ContractArtifact(BaseModel):
    config_path: Path
    config_text: str
    contracts: list[dict]


def build_cluster_dag(plan: RefactorPlan, graph_json_path: Path) -> nx.DiGraph:
    """Build inter-cluster import edges as a directed graph.

    Nodes = pkg_NNN names (cluster names).
    Edges = src → tgt cluster imports (multi-edges collapsed).
    Rationale nodes are skipped.

    Returns a directed graph where an edge (a, b) means cluster a imports from cluster b.
    """
    G = load_graph(graph_json_path)

    # Build node → cluster_name mapping from plan.clusters.
    node_to_cluster: dict[str, str] = {}
    for cluster in plan.clusters:
        community_id = cluster.community_id
        cluster_name = cluster.name
        # Map all nodes in this community to the cluster name.
        for node, data in G.nodes(data=True):
            if data.get("community") == community_id:
                node_to_cluster[node] = cluster_name

    # Build inter-cluster DAG.
    dag = nx.DiGraph()

    # Add all cluster nodes to ensure they all appear in the DAG.
    for cluster in plan.clusters:
        dag.add_node(cluster.name)

    # Walk graph edges; add inter-cluster edges to DAG.
    for u, v, data in G.edges(data=True):
        # Skip rationale nodes.
        if "rationale" in u or "rationale" in v:
            continue

        u_cluster = node_to_cluster.get(u)
        v_cluster = node_to_cluster.get(v)

        # Only add edge if both endpoints map to clusters and they differ.
        if u_cluster and v_cluster and u_cluster != v_cluster:
            # Add edge from source cluster to target cluster.
            # (Assumes edge direction is meaningful; graphify edges go from source to target.)
            if not dag.has_edge(u_cluster, v_cluster):
                dag.add_edge(u_cluster, v_cluster, data=data)

    return dag


def emit_contract(
    plan: RefactorPlan,
    _view: GraphView,
    graph_json_path: Path,
    repo_root: Path,
    *,
    root_package: str = "messy_pkg",
) -> ContractArtifact:
    """Generate .importlinter config and contract details.

    Returns:
        ContractArtifact with config_path, config_text, and contracts list.
        Writes .importlinter under repo_root.
    """
    dag = build_cluster_dag(plan, graph_json_path)

    # Load the full graph to find AMBIGUOUS edges.
    G = load_graph(graph_json_path)

    # Build node → cluster mapping (same as in build_cluster_dag).
    node_to_cluster: dict[str, str] = {}
    for cluster in plan.clusters:
        community_id = cluster.community_id
        cluster_name = cluster.name
        for node, data in G.nodes(data=True):
            if data.get("community") == community_id:
                node_to_cluster[node] = cluster_name

    contracts: list[dict] = []
    config_lines: list[str] = []

    # [importlinter] section.
    config_lines.append("[importlinter]")
    config_lines.append(f"root_package = {root_package}")
    config_lines.append("")

    # Determine if DAG is acyclic.
    is_acyclic = nx.is_directed_acyclic_graph(dag)

    # --- Layers contract (if acyclic) ---
    if is_acyclic:
        topo_order = list(nx.topological_sort(dag))
        contracts.append(
            {
                "type": "layers",
                "name": "pkg_NNN layered architecture",
                "modules": topo_order,
            }
        )
        config_lines.append("[importlinter:contract:layers]")
        config_lines.append("name = pkg_NNN layered architecture")
        config_lines.append("type = layers")
        config_lines.append("layers =")
        for cluster in topo_order:
            config_lines.append(f"    {root_package}.{cluster}")
        config_lines.append(f"containers = {root_package}")
        config_lines.append("")
    else:
        # If cyclic, add a comment explaining.
        config_lines.append("# WARNING: cluster DAG has cycles; layers contract omitted.")
        config_lines.append("")

    # --- Independence contract ---
    # Find cluster pairs with no inter-cluster edges (independent clusters).
    # For MVP, emit a single independence contract with all clusters that have
    # no outgoing or incoming edges (isolated nodes), plus those with no path
    # between them. For simplicity, we identify nodes with in_degree + out_degree == 0.
    independent: list[str] = []
    for cluster in plan.clusters:
        if dag.in_degree(cluster.name) == 0 and dag.out_degree(cluster.name) == 0:
            independent.append(cluster.name)

    # If we have any independent clusters, emit an independence contract.
    if independent:
        independent.sort()
        contracts.append(
            {
                "type": "independence",
                "name": "independent clusters",
                "modules": independent,
            }
        )
        config_lines.append("[importlinter:contract:independence]")
        config_lines.append("name = independent clusters")
        config_lines.append("type = independence")
        config_lines.append("modules =")
        for cluster in independent:
            config_lines.append(f"    {root_package}.{cluster}")
        config_lines.append("")

    # --- Forbidden contract for AMBIGUOUS edges ---
    # Find all edges in the original graph with confidence == "AMBIGUOUS".
    # Group by (src_cluster, tgt_cluster).
    ambiguous_pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}  # (src, tgt) -> list of (u, v) node-pairs

    for u, v, data in G.edges(data=True):
        if "rationale" in u or "rationale" in v:
            continue
        if data.get("confidence") == "AMBIGUOUS":
            u_cluster = node_to_cluster.get(u)
            v_cluster = node_to_cluster.get(v)
            if u_cluster and v_cluster and u_cluster != v_cluster:
                key = (u_cluster, v_cluster)
                if key not in ambiguous_pairs:
                    ambiguous_pairs[key] = []
                ambiguous_pairs[key].append((u, v))

    # Emit one forbidden contract per ambiguous pair.
    for (src_cluster, tgt_cluster), _pairs in sorted(ambiguous_pairs.items()):
        contract_id = f"forbidden_ambiguous_{src_cluster}_{tgt_cluster}"
        contracts.append(
            {
                "type": "forbidden",
                "name": f"Forbid ambiguous: {src_cluster} -> {tgt_cluster}",
                "source_modules": f"{root_package}.{src_cluster}",
                "forbidden_modules": f"{root_package}.{tgt_cluster}",
            }
        )
        config_lines.append(f"[importlinter:contract:{contract_id}]")
        config_lines.append(f"name = Forbid ambiguous: {src_cluster} -> {tgt_cluster}")
        config_lines.append("type = forbidden")
        config_lines.append(f"source_modules = {root_package}.{src_cluster}")
        config_lines.append(f"forbidden_modules = {root_package}.{tgt_cluster}")
        config_lines.append("")

    # --- Acyclic siblings contract between all pkg_NNN ---
    # All clusters are pkg_NNN siblings (flat package structure).
    pkg_clusters = [c.name for c in plan.clusters if c.name.startswith("pkg_")]
    if len(pkg_clusters) > 1:
        pkg_clusters.sort()
        contracts.append(
            {
                "type": "acyclic_siblings",
                "name": "no cycles between pkg_NNN siblings",
                "modules": pkg_clusters,
                "ancestors": [root_package],
            }
        )
        config_lines.append("[importlinter:contract:acyclic_siblings]")
        config_lines.append("name = no cycles between pkg_NNN siblings")
        config_lines.append("type = acyclic_siblings")
        config_lines.append("modules =")
        for cluster in pkg_clusters:
            config_lines.append(f"    {root_package}.{cluster}")
        config_lines.append("ancestors =")
        config_lines.append(f"    {root_package}")
        config_lines.append("")

    config_text = "\n".join(config_lines)

    # Write .importlinter under repo_root.
    config_path = repo_root / ".importlinter"
    config_path.write_text(config_text)

    return ContractArtifact(
        config_path=config_path,
        config_text=config_text,
        contracts=contracts,
    )
