


# ---------------------------------------------------------------------------
# Cluster naming (context only — Claude Code supplies the names)
# ---------------------------------------------------------------------------

from pathlib import Path
import networkx as nx
from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.planning.models import RefactorPlan

def _cohesion_prose(coh: float | None, n_files: int, is_scattered: bool, n_dirs: int) -> str:
    spread = f" spread across {n_dirs} directories" if is_scattered else " in one directory"
    if coh is None:
        return f"{n_files} files{spread}. Cohesion unknown."
    if coh < 0.10:
        coupling = "almost no structural coupling — files may share a directory by accident"
    elif coh < 0.20:
        coupling = "weak structural coupling — review before treating as correctly placed"
    elif coh < 0.40:
        coupling = "moderate internal coupling"
    else:
        coupling = "strong internal dependencies"
    suffix = " Do these files actually call each other?" if not is_scattered and coh < 0.20 else ""
    return f"{n_files} files{spread}. Cohesion {coh:.2f} — {coupling}.{suffix}"




def _format_dep_direction(
    cross_cluster_edges: list[dict], community_files: set[str], root: Path
) -> list[str]:
    outgoing: dict[str, int] = {}
    incoming: dict[str, int] = {}
    for e in cross_cluster_edges:
        sf = e.get("source_file", "")
        tf = e.get("target_file", "")
        direction = e.get("direction") or ("outgoing" if sf in community_files else "incoming")
        if direction == "outgoing":
            try:
                rel = str(Path(tf).relative_to(root)) if tf else tf
            except ValueError:
                rel = tf
            if rel:
                outgoing[rel] = outgoing.get(rel, 0) + 1
        else:
            try:
                rel = str(Path(sf).relative_to(root)) if sf else sf
            except ValueError:
                rel = sf
            if rel:
                incoming[rel] = incoming.get(rel, 0) + 1

    lines: list[str] = []
    if outgoing or incoming:
        lines.append("Dependencies:")
        if outgoing:
            parts = ", ".join(
                f"{f} ({c})" for f, c in sorted(outgoing.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"  Outgoing (this calls): {parts}")
        if incoming:
            parts = ", ".join(
                f"{f} ({c})" for f, c in sorted(incoming.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"  Incoming (callers): {parts}")
    return lines




def _norm_sf(sf: str, root: Path) -> str:
    """Normalize a graphify source_file to an absolute path string."""
    if not sf:
        return sf
    p = Path(sf)
    return str(p) if p.is_absolute() else str((root / p).resolve())




def _build_file_node_map(G: nx.Graph, root: Path) -> dict[str, list[str]]:
    """Map absolute file paths → list of node IDs.

    Graphify stores source_file as either absolute or relative paths depending
    on how files were extracted. This normalises both to absolute for consistent
    membership tests.
    """
    mapping: dict[str, list[str]] = {}
    for nid, attrs in G.nodes(data=True):
        if attrs.get("file_type") == "rationale":
            continue
        raw_sf = attrs.get("source_file", "")
        if not raw_sf:
            continue
        abs_sf = _norm_sf(raw_sf, root)
        mapping.setdefault(abs_sf, []).append(nid)
    return mapping




def _compute_file_roles(
    source_files: list[str],
    G: nx.Graph,
    god_node_source_files: set[str],
    bridge_node_ids: set[str],
    isolated_node_ids: set[str],
    file_node_map: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Classify each file as hub / bridge / leaf / isolated.

    Hub: from graphify's god_nodes.
    Bridge: from graphify's _is_file_node/_is_concept_node-filtered betweenness (ClusterView.bridge_nodes).
    Isolated: from graphify's _is_file_node/_is_concept_node-filtered degree ≤ 1 (ClusterView.isolated_nodes).
    Leaf: low total degree with asymmetric in/out ratio — not in graphify.
    """
    result: dict[str, list[str]] = {f: [] for f in source_files}

    for f in source_files:
        nodes = file_node_map.get(f, [])
        total_deg = sum(G.degree(n) for n in nodes) if nodes else 0
        roles: list[str] = []

        if f in god_node_source_files:
            roles.append("hub")
        if any(n in bridge_node_ids for n in nodes):
            roles.append("bridge")
        if any(n in isolated_node_ids for n in nodes):
            roles.append("isolated")
        elif total_deg <= 6 and isinstance(G, nx.DiGraph):
            in_deg = sum(G.in_degree(n) for n in nodes)
            out_deg = sum(G.out_degree(n) for n in nodes)
            if in_deg > 0 and out_deg <= 1:
                roles.append("leaf")

        result[f] = roles

    return result




def _format_pending_decisions(
    plan: RefactorPlan, root: Path, view: ClusterView | None = None
) -> str:
    if not plan.pending_decisions:
        return ""

    god_node_files: set[str] = set()
    file_node_map: dict[str, list[str]] = {}
    if view is not None:
        file_node_map = _build_file_node_map(view.G, root)
        for g in view.god_nodes:
            sf = _norm_sf(g.get("source_file", ""), root)
            if sf:
                god_node_files.add(sf)

    # Categorise communities by what action they need
    need_placement = [d for d in plan.pending_decisions if d.needs_placement]
    need_review_ids: set[int] = set()
    need_review = []
    for d in plan.pending_decisions:
        if not d.needs_placement:
            if (d.cohesion is not None and d.cohesion < 0.20) or d.surprising_connections:
                need_review.append(d)
                need_review_ids.add(d.community_id)
    no_action = [
        d for d in plan.pending_decisions
        if not d.needs_placement and d.community_id not in need_review_ids
    ]

    lines: list[str] = []

    # Action list — agent reads this first to scope its work
    if need_placement:
        ids = ", ".join(str(d.community_id) for d in need_placement)
        lines.append(f"Decisions required: communities {ids}")
    if need_review:
        ids = ", ".join(str(d.community_id) for d in need_review)
        lines.append(f"Review warranted: communities {ids} (co-located but weak coupling or surprising connections)")
    if no_action:
        ids = ", ".join(str(d.community_id) for d in no_action)
        lines.append(f"No action needed: communities {ids}")
    lines.append("")

    # Detail blocks — only for communities that need a decision or review
    for d in need_placement + need_review:
        community_files = set(d.source_files)
        n_dirs = len(d.current_dirs)

        if d.needs_placement:
            lines.append(f"--- Community {d.community_id} [PLACEMENT NEEDED] ---")
        else:
            single_dir = next(iter(d.current_dirs))
            try:
                dir_rel = Path(single_dir).relative_to(root)
            except ValueError:
                dir_rel = Path(single_dir)
            lines.append(f"--- Community {d.community_id} [REVIEW: co-located in {dir_rel}/] ---")

        lines.append(_cohesion_prose(d.cohesion, len(d.source_files), d.needs_placement, n_dirs))
        lines.append("")

        if d.needs_placement:
            lines.append("Current directories:")
            for dir_path, files in d.current_dirs.items():
                try:
                    rel = Path(dir_path).relative_to(root)
                except ValueError:
                    rel = Path(dir_path)
                indicator = "  ← spread" if n_dirs > 1 else ""
                lines.append(f"  {rel}/  ({len(files)} file{'s' if len(files) > 1 else ''}){indicator}")
            lines.append("")

        if view is not None:
            roles_map = _compute_file_roles(
                d.source_files, view.G, god_node_files,
                view.bridge_nodes, view.isolated_nodes, file_node_map,
            )
        else:
            roles_map = {}
        lines.append("Files:")
        for f in d.source_files:
            try:
                rel_f = Path(f).relative_to(root)
            except ValueError:
                rel_f = Path(f)
            role_tags = roles_map.get(f, [])
            tag_str = f"  [{', '.join(role_tags)}]" if role_tags else ""
            lines.append(f"  {rel_f}{tag_str}")
        lines.append("")

        dep_lines = _format_dep_direction(d.cross_cluster_edges, community_files, root)
        if dep_lines:
            lines.extend(dep_lines)
            lines.append("")

        if d.surprising_connections:
            parts = []
            for s in d.surprising_connections[:3]:
                src = s.get("source", "")
                tgt = s.get("target", "")
                conf = s.get("confidence", "")
                score = s.get("confidence_score", "")
                conf_str = f" [{conf}" + (f", {score:.2f}" if isinstance(score, float) else "") + "]"
                parts.append(f"{src} ↔ {tgt}{conf_str}")
            lines.append("Surprising: " + "; ".join(parts))
            lines.append("")

    return "\n".join(lines)
