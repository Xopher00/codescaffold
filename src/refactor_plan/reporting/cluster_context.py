


# ---------------------------------------------------------------------------
# Cluster naming (context only — Claude Code supplies the names)
# ---------------------------------------------------------------------------

from pathlib import Path
import networkx as nx
from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.planning.proposal import RefactorPlan

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




def _community_alias(source_files: list[str], G: nx.Graph, file_node_map: dict[str, list[str]], root: Path) -> str:
    """Derive a stable human-readable alias for a community.

    Single directory: alias = that directory (e.g. 'execution/').
    Scattered: alias = basename of the highest-degree file (changes predictably with membership).
    Single file: alias = that filename.
    """
    if not source_files:
        return "?"
    parents = {str(Path(f).parent) for f in source_files}
    if len(parents) == 1:
        try:
            rel = str(Path(source_files[0]).parent.relative_to(root))
        except ValueError:
            rel = Path(source_files[0]).parent.name
        return (rel + "/") if (rel and rel != ".") else (Path(source_files[0]).parent.name + "/")
    # Scattered — highest-degree file wins
    best_file, best_deg = None, -1
    for f in source_files:
        deg = sum(G.degree(n) for n in file_node_map.get(f, []))
        if deg > best_deg:
            best_deg, best_file = deg, f
    if best_file:
        try:
            return str(Path(best_file).relative_to(root))
        except ValueError:
            return Path(best_file).name
    return Path(source_files[0]).name


def _build_node_to_file(file_node_map: dict[str, list[str]]) -> dict[str, str]:
    """Invert file_node_map: node_id → abs file path."""
    return {nid: f for f, nids in file_node_map.items() for nid in nids}


def _file_edge_breakdown(
    G: nx.Graph,
    file_abs: str,
    community_files: set[str],
    file_node_map: dict[str, list[str]],
    node_to_file: dict[str, str],
    root: Path,
) -> tuple[int, int, list[tuple[str, int]]]:
    """Return (internal, total, ext_dirs) for cross-file edges from file_abs.

    internal: edges to nodes in other files within community_files
    total: internal + external cross-file edges
    ext_dirs: [(dir_rel, count), ...] sorted by count desc (external dirs only)
    """
    my_nodes = set(file_node_map.get(file_abs, []))
    community_nodes: set[str] = set()
    for cf in community_files:
        if cf != file_abs:
            community_nodes.update(file_node_map.get(cf, []))

    internal = 0
    ext_dir_counts: dict[str, int] = {}
    for n in my_nodes:
        for nb in G.neighbors(n):
            if nb in my_nodes:
                continue
            # Only count outgoing edges using graphify's _src attribute.
            # Incoming edges (e.g. test files importing this file) are noise
            # for the internal-ratio metric and must be excluded.
            edge_data = G.edges[n, nb]
            if edge_data.get("_src", n) != n:
                continue
            if nb in community_nodes:
                internal += 1
            else:
                nb_file = node_to_file.get(nb, "")
                if nb_file:
                    try:
                        d = str(Path(nb_file).relative_to(root).parent)
                    except ValueError:
                        d = Path(nb_file).parent.name
                    ext_dir_counts[d] = ext_dir_counts.get(d, 0) + 1

    ext_dirs = sorted(ext_dir_counts.items(), key=lambda x: -x[1])
    total = internal + sum(ext_dir_counts.values())
    return internal, total, ext_dirs


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




_CLEAR_THRESHOLD = 0.80


def _format_pending_decisions(
    plan: RefactorPlan, root: Path, view: ClusterView | None = None
) -> str:
    if not plan.pending_decisions:
        return ""

    from collections import Counter, defaultdict

    god_node_files: set[str] = set()
    file_node_map: dict[str, list[str]] = {}
    node_to_file: dict[str, str] = {}
    comm_aliases: dict[int, str] = {}
    if view is not None:
        file_node_map = _build_file_node_map(view.G, root)
        node_to_file = _build_node_to_file(file_node_map)
        for g in view.god_nodes:
            sf = _norm_sf(g.get("source_file", ""), root)
            if sf:
                god_node_files.add(sf)
        for d in plan.pending_decisions:
            comm_aliases[d.community_id] = _community_alias(d.source_files, view.G, file_node_map, root)

    # Per-file classification into CLEAR / CONTESTED
    # clear_by_dest: dest_dir → [(rel_path, internal, total)]
    clear_by_dest: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    # contested_entries: list of {rel, comm_id, internal, total, ext_dirs, roles}
    contested_entries: list[dict] = []
    contested_comm_ids: set[int] = set()

    for d in plan.pending_decisions:
        community_files = set(d.source_files)

        if view is not None:
            roles_map = _compute_file_roles(
                d.source_files, view.G, god_node_files,
                view.bridge_nodes, view.isolated_nodes, file_node_map,
            )
        else:
            roles_map = {}

        # Destination recommendation: dominant parent dir within this community
        dir_counts: Counter = Counter(str(Path(f).parent) for f in d.source_files)
        dominant_dir = dir_counts.most_common(1)[0][0] if dir_counts else ""

        for f in d.source_files:
            try:
                rel = str(Path(f).relative_to(root))
            except ValueError:
                rel = f

            if view is not None:
                internal, total, ext_dirs = _file_edge_breakdown(
                    view.G, f, community_files, file_node_map, node_to_file, root
                )
            else:
                internal, total, ext_dirs = 0, 0, []

            ratio = internal / total if total > 0 else 1.0

            if ratio >= _CLEAR_THRESHOLD:
                clear_by_dest[dominant_dir].append((rel, internal, total))
            else:
                contested_comm_ids.add(d.community_id)
                contested_entries.append({
                    "rel": rel,
                    "comm_id": d.community_id,
                    "internal": internal,
                    "total": total,
                    "ext_dirs": ext_dirs,
                    "roles": roles_map.get(f, []),
                })

        # Communities that need_placement but all files are CLEAR still require a
        # placement decision — flag them as contested so detail block is shown.
        if d.needs_placement and d.community_id not in contested_comm_ids:
            contested_comm_ids.add(d.community_id)

    lines: list[str] = []

    # ── CLEAR section ────────────────────────────────────────────────────────
    if clear_by_dest:
        lines.append("CLEAR (move together, no decision needed)")
        for dest_dir in sorted(clear_by_dest):
            entries = clear_by_dest[dest_dir]
            try:
                dir_rel = str(Path(dest_dir).relative_to(root))
            except ValueError:
                dir_rel = Path(dest_dir).name
            lines.append(f"  → {dir_rel}/")
            for rel, internal, total in sorted(entries):
                pct = f"{internal}/{total} edges internal" if total > 0 else "no cross-file edges"
                lines.append(f"    {rel:<55}  {pct}")
        lines.append("")

    # ── CONTESTED section ─────────────────────────────────────────────────────
    if contested_entries:
        lines.append("CONTESTED (placement decision needed)")
        for e in sorted(contested_entries, key=lambda x: x["rel"]):
            role_str = f"  [{', '.join(e['roles'])}]" if e["roles"] else ""
            alias = comm_aliases.get(e["comm_id"], "")
            alias_str = f"  [comm_{e['comm_id']}: {alias}]" if alias else f"  [comm_{e['comm_id']}]"
            if e["ext_dirs"]:
                dest_str = ", ".join(f"{cnt} edges → {d}/" for d, cnt in e["ext_dirs"][:3])
                lines.append(f"  {e['rel']}{role_str}{alias_str}")
                lines.append(f"    {dest_str}")
            else:
                lines.append(f"  {e['rel']}{role_str}{alias_str}  — mixed signals, no dominant external destination")
        lines.append("")

    # ── Detail blocks for contested communities ───────────────────────────────
    contested_decisions = [d for d in plan.pending_decisions if d.community_id in contested_comm_ids]
    for d in contested_decisions:
        community_files = set(d.source_files)
        n_dirs = len(d.current_dirs)

        alias = comm_aliases.get(d.community_id, "")
        alias_tag = f" [alias: {alias}]" if alias else ""
        if d.needs_placement:
            lines.append(f"--- Community {d.community_id}{alias_tag} [PLACEMENT NEEDED] ---")
        else:
            single_dir = next(iter(d.current_dirs))
            try:
                dir_rel = Path(single_dir).relative_to(root)
            except ValueError:
                dir_rel = Path(single_dir)
            lines.append(f"--- Community {d.community_id}{alias_tag} [CONTESTED: co-located in {dir_rel}/] ---")

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
