from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
from pydantic import BaseModel

from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.planning.planner import RefactorPlan

_MAX_SYMBOLS = 6   # classes or functions shown per cluster
_MAX_DEPS = 4      # cross-cluster dependency labels shown


class RenameEntry(BaseModel):
    old_name: str
    new_name: str
    rationale: str = ""


class RenameMap(BaseModel):
    entries: list[RenameEntry] = []


# ---------------------------------------------------------------------------
# Graph context extraction
# ---------------------------------------------------------------------------

def _build_cluster_context(
    community_id: int,
    source_files: list[str],
    G: nx.Graph,
    all_file_communities: dict[int, list[str]],
) -> dict[str, list[str]]:
    """Extract classes, functions, and cross-cluster dependencies for one cluster."""
    file_set = set(source_files)

    # Map every node to its community so we can label cross-cluster edges
    node_to_comm: dict[str, int] = {}
    for comm_id, comm_files in all_file_communities.items():
        comm_file_set = set(comm_files)
        for nid, attrs in G.nodes(data=True):
            if attrs.get("source_file") in comm_file_set:
                node_to_comm[nid] = comm_id

    cluster_nodes: set[str] = {
        nid for nid, attrs in G.nodes(data=True)
        if attrs.get("source_file") in file_set
    }

    classes: list[str] = []
    functions: list[str] = []

    for nid in cluster_nodes:
        label: str = G.nodes[nid].get("label", "")
        if label.endswith(".py"):
            continue  # file node — not useful for naming
        if label.startswith("."):
            continue  # method node (e.g. ".authenticate()") — too granular
        if label.endswith("()"):
            functions.append(label[:-2])
        else:
            classes.append(label)

    # Cross-cluster uses/calls edges — tells Claude what this cluster depends on
    external_deps: list[str] = []
    for src, dst, attrs in G.edges(data=True):
        relation = attrs.get("relation", "")
        if relation not in ("uses", "calls"):
            continue
        src_in = src in cluster_nodes
        dst_in = dst in cluster_nodes
        if src_in == dst_in:
            continue  # both inside or both outside
        ext_node = dst if src_in else src
        ext_comm = node_to_comm.get(ext_node)
        if ext_comm is None or ext_comm == community_id:
            continue
        ext_label = G.nodes[ext_node].get("label", ext_node)
        external_deps.append(f"{ext_label} [pkg_{ext_comm:03d}]")

    return {
        "classes": sorted(set(classes))[:_MAX_SYMBOLS],
        "functions": sorted(set(functions))[:_MAX_SYMBOLS],
        "external_deps": sorted(set(external_deps))[:_MAX_DEPS],
    }


def _format_cluster_block(
    placeholder: str,
    file_names: list[str],
    ctx: dict[str, list[str]],
) -> str:
    lines = [f"- {placeholder}:"]
    lines.append(f"  Files: {', '.join(file_names)}")
    if ctx["classes"]:
        lines.append(f"  Classes: {', '.join(ctx['classes'])}")
    if ctx["functions"]:
        lines.append(f"  Functions: {', '.join(ctx['functions'])}")
    if ctx["external_deps"]:
        lines.append(f"  Uses from other clusters: {', '.join(ctx['external_deps'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_naming_context(refactor_plan: RefactorPlan, view: ClusterView) -> str:
    """Return formatted cluster context for naming — no API call.

    Each block lists a placeholder package's files, classes, functions, and
    cross-cluster dependencies.  Pass the result to an LLM and ask it to
    return a JSON map of placeholder → snake_case name.
    """
    clusters_with_placeholder = [
        c for c in refactor_plan.clusters if c.proposed_package
    ]
    if not clusters_with_placeholder:
        return ""

    blocks: list[str] = []
    for c in clusters_with_placeholder:
        placeholder = f"pkg_{c.community_id:03d}"
        file_names = [Path(sf).name for sf in c.source_files]
        ctx = _build_cluster_context(
            c.community_id, c.source_files, view.G, view.file_communities
        )
        blocks.append(_format_cluster_block(placeholder, file_names, ctx))
    return "\n\n".join(blocks)


def write_rename_map(rename_map: RenameMap, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rename_map.model_dump_json(indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_json_fence(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers Claude sometimes adds."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text
