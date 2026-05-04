from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
from pydantic import BaseModel
from refactor_plan.planning import RefactorPlan
from refactor_plan.interface import ClusterView

if TYPE_CHECKING:
    pass

_MAX_SYMBOLS = 6    # classes or functions shown per cluster
_MAX_DEPS = 4       # cross-cluster dependency labels shown
_MAX_GODS = 3       # god-node labels shown per cluster

# Minimum confidence for INFERRED edges to be included in dependency summaries
_INFERRED_CONFIDENCE_THRESHOLD = 0.7

# Relations that indicate structural dependency between modules
_STRUCTURAL_RELATIONS = frozenset(("uses", "calls", "contains", "inherits", "imports_from"))


class RenameEntry(BaseModel):
    """A single LLM-approved rename binding a placeholder package or module name to its intended semantic name."""
    old_name: str
    new_name: str
    rationale: str = ""


class RenameMap(BaseModel):
    """Ordered collection of RenameEntry objects representing the complete semantic rename map for a refactor pass."""
    entries: list[RenameEntry] = []


# ---------------------------------------------------------------------------
# Graph context extraction
# ---------------------------------------------------------------------------

def _build_cluster_context(
    community_id: int,
    source_files: list[str],
    G: nx.Graph,
    all_file_communities: dict[int, list[str]],
    cohesion: float | None = None,
    cluster_god_nodes: list[str] | None = None,
) -> dict:
    """Extract classes, functions, cross-cluster dependencies, and graph metrics for one cluster."""
    file_set = set(source_files)

    # Map every non-rationale node to its community for cross-cluster edge labelling
    node_to_comm: dict[str, int] = {}
    for comm_id, comm_files in all_file_communities.items():
        comm_file_set = set(comm_files)
        for nid, attrs in G.nodes(data=True):
            if attrs.get("source_file") in comm_file_set:
                node_to_comm[nid] = comm_id

    # Exclude rationale nodes — they describe design intent, not code structure
    cluster_nodes: set[str] = {
        nid for nid, attrs in G.nodes(data=True)
        if attrs.get("source_file") in file_set
        and attrs.get("file_type") != "rationale"
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

    # Cross-cluster structural edges, filtered by relation type and confidence
    external_deps: list[str] = []
    for src, dst, attrs in G.edges(data=True):
        relation = attrs.get("relation", "")
        if relation not in _STRUCTURAL_RELATIONS:
            continue
        # Skip weak inferred edges — they add noise without adding signal
        if (attrs.get("confidence") == "INFERRED"
                and attrs.get("confidence_score", 1.0) < _INFERRED_CONFIDENCE_THRESHOLD):
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
        "cohesion": cohesion,
        "god_nodes": (cluster_god_nodes or [])[:_MAX_GODS],
    }


def _format_cluster_block(
    placeholder: str,
    file_names: list[str],
    ctx: dict,
) -> str:
    lines = [f"- {placeholder}:"]
    lines.append(f"  Files: {', '.join(file_names)}")
    if ctx["classes"]:
        lines.append(f"  Classes: {', '.join(ctx['classes'])}")
    if ctx["functions"]:
        lines.append(f"  Functions: {', '.join(ctx['functions'])}")
    if ctx["external_deps"]:
        lines.append(f"  Uses from other clusters: {', '.join(ctx['external_deps'])}")
    if ctx.get("god_nodes"):
        lines.append(f"  Core abstractions: {', '.join(ctx['god_nodes'])}")
    if ctx.get("cohesion") is not None:
        coh = ctx["cohesion"]
        flag = " (loosely coupled — consider splitting)" if coh < 0.1 else ""
        lines.append(f"  Cohesion: {coh:.2f}{flag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_naming_context(refactor_plan: RefactorPlan, view: ClusterView) -> str:
    """Return formatted cluster context for each placeholder package, ready to pass to an LLM for semantic naming."""
    clusters_with_placeholder = [
        c for c in refactor_plan.clusters if c.proposed_package
    ]
    if not clusters_with_placeholder:
        return ""

    # Index god nodes by source file so we can pick the ones per cluster
    god_nodes_by_file: dict[str, list[str]] = {}
    for g in view.god_nodes:
        nid = g.get("identifier", "")
        label = g.get("label", nid)
        sf = view.G.nodes.get(nid, {}).get("source_file", "")
        if sf:
            god_nodes_by_file.setdefault(sf, []).append(label)

    blocks: list[str] = []
    for c in clusters_with_placeholder:
        placeholder = f"pkg_{c.community_id:03d}"
        file_names = [Path(sf).name for sf in c.source_files]
        cohesion = view.cohesion.get(c.community_id)

        # Collect god-node labels whose source file belongs to this cluster
        cluster_gods: list[str] = []
        for sf in c.source_files:
            cluster_gods.extend(god_nodes_by_file.get(sf, []))

        ctx = _build_cluster_context(
            c.community_id,
            c.source_files,
            view.G,
            view.file_communities,
            cohesion=cohesion,
            cluster_god_nodes=cluster_gods,
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

def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers Claude sometimes adds."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text
