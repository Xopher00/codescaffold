from __future__ import annotations

from pathlib import Path

import networkx as nx
from refactor_plan.execution.models import ApplyResult, MoveKind, MoveStrategy
from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.planning.planner import RefactorPlan


def _risk(kind: str, source: str, dest: str) -> str:
    if kind == MoveKind.SYMBOL:
        return "HIGH"
    src_parent = str(Path(source).parent)
    dst_parent = str(Path(dest).parent)
    return "LOW" if src_parent == dst_parent else "MEDIUM"


def render_dry_run_report(plan: dict, repo_root: str) -> str:
    file_moves: list[dict] = plan.get("file_moves", [])
    symbol_moves: list[dict] = plan.get("symbol_moves", [])
    communities: list = plan.get("communities", [])
    surprising: list[dict] = plan.get("surprising_connections", [])
    god_nodes: list[dict] = plan.get("god_nodes", [])

    lines: list[str] = []
    lines.append("# Structure Report\n")

    pending_decisions: list[dict] = plan.get("pending_decisions", [])
    placement_needed = sum(1 for d in pending_decisions if d.get("needs_placement"))

    lines.append("## Summary\n")
    lines.append(f"- Communities detected: {len(communities)}")
    if pending_decisions:
        lines.append(f"- Pending placement decisions: {placement_needed}")
        lines.append(f"- Communities already well-placed: {len(pending_decisions) - placement_needed}")
    else:
        lines.append(f"- File moves approved: {len(file_moves)}")
    lines.append(f"- Symbol moves proposed: {len(symbol_moves)}")
    lines.append(f"- Repository root: `{repo_root}`\n")

    # Cluster analysis — cohesion scores and risk flags
    clusters_with_cohesion = [c for c in communities if c.get("cohesion") is not None]
    if clusters_with_cohesion:
        lines.append("## Cluster Analysis\n")
        lines.append("| Cluster | Files | Cohesion | Risk |")
        lines.append("|---------|-------|----------|------|")
        for c in sorted(clusters_with_cohesion, key=lambda x: x.get("cohesion", 1.0)):
            cid = c.get("community_id", "?")
            n_files = len(c.get("source_files", []))
            coh = c.get("cohesion", 0.0)
            risk = c.get("risk_level", "LOW")
            pkg = c.get("proposed_package")
            label = f"pkg_{cid:03d}" if pkg else f"comm_{cid} (no move)"
            lines.append(f"| {label} | {n_files} | {coh:.2f} | {risk} |")
        lines.append("")

    # God nodes — most structurally central nodes in the graph
    if god_nodes:
        lines.append("## Core Abstractions (God Nodes)\n")
        for g in god_nodes[:8]:
            label = g.get("label", g.get("identifier", "?"))
            sf = g.get("source_file", "")
            edges = g.get("edges", "?")
            lines.append(f"- **{label}** ({edges} edges) — `{sf}`")
        lines.append("")

    # Surprising connections — unexpected cross-community dependencies
    if surprising:
        lines.append("## Surprising Connections\n")
        for s in surprising[:6]:
            src = s.get("source_label", s.get("source", "?"))
            dst = s.get("target_label", s.get("target", "?"))
            rel = s.get("relation", "→")
            conf = s.get("confidence_score", s.get("confidence", ""))
            conf_str = f" (confidence: {conf:.2f})" if isinstance(conf, float) else ""
            lines.append(f"- `{src}` **{rel}** `{dst}`{conf_str}")
        lines.append("")

    if pending_decisions:
        lines.append("## Pending Decisions\n")
        lines.append("Run `get_cluster_context` to see graph evidence and decide placements.\n")
        lines.append("| Community | Files | Directories | Status |")
        lines.append("|-----------|-------|-------------|--------|")
        for d in pending_decisions:
            cid = d.get("community_id", "?")
            n_files = len(d.get("source_files", []))
            dirs = d.get("current_dirs", {})
            n_dirs = len(dirs)
            status = "PLACEMENT NEEDED" if d.get("needs_placement") else "co-located"
            lines.append(f"| {cid} | {n_files} | {n_dirs} | {status} |")
        lines.append("")
    elif file_moves:
        lines.append("## Approved File Moves\n")
        lines.append("| Source | Destination | Risk |")
        lines.append("|--------|-------------|------|")
        for m in file_moves:
            src, dst = m.get("source", ""), m.get("dest", "")
            risk = _risk("FILE", src, dst)
            lines.append(f"| `{src}` | `{dst}` | {risk} |")
        lines.append("")

    if symbol_moves:
        lines.append("## Symbol Moves\n")
        lines.append("| Source | Destination | Symbol | Risk |")
        lines.append("|--------|-------------|--------|------|")
        for m in symbol_moves:
            src = m.get("source", "")
            dst = m.get("dest", "")
            sym = m.get("symbol", "")
            lines.append(f"| `{src}` | `{dst}` | `{sym}` | HIGH |")
        lines.append("")

    lines.append("## Validation Plan\n")
    for cmd in plan.get("validation_commands", ["python -m compileall .", "pytest", "ruff check ."]):
        lines.append(f"- `{cmd}`")
    lines.append("")

    lines.append("## Known Limitations\n")
    lines.append("- Symbol moves use LibCST for syntax-preserving extraction.")
    lines.append("- Cross-package moves may require manual import shim review.")
    lines.append("- Placeholder names are intentional; semantic renaming is a later phase.")

    return "\n".join(lines) + "\n"


def render_apply_report(result: ApplyResult) -> str:
    lines: list[str] = []
    lines.append("# Apply Report\n")

    lines.append("## Applied\n")
    lines.append(f"Total applied: {len(result.applied)}\n")
    if result.applied:
        lines.append("| Source | Destination | Strategy | Imports Rewritten | Validation |")
        lines.append("|--------|-------------|----------|-------------------|------------|")
        for a in result.applied:
            strat = a.strategy.value if a.strategy else "—"
            valid = "pass" if a.validation_passed else ("fail" if a.validation_passed is False else "—")
            lines.append(f"| `{a.source}` | `{a.dest}` | {strat} | {a.imports_rewritten} | {valid} |")
        lines.append("")

    escalated = result.skipped + result.failed + result.blocked
    if escalated:
        lines.append("## Escalated / Failed / Blocked\n")
        lines.append("| Source | Category | Reason | Strategy Attempted |")
        lines.append("|--------|----------|--------|--------------------|")
        for e in escalated:
            strat = e.strategy_attempted.value if e.strategy_attempted else "—"
            lines.append(f"| `{e.source}` | {e.category} | {e.reason} | {strat} |")
        lines.append("")

    lines.append("## Strategy Summary\n")
    rope_count = sum(1 for a in result.applied if a.strategy == MoveStrategy.ROPE)
    libcst_count = sum(1 for a in result.applied if a.strategy == MoveStrategy.LIBCST)
    lines.append(f"- rope: {rope_count}")
    lines.append(f"- libcst: {libcst_count}")
    lines.append("")

    lines.append("## Validation Results\n")
    passed = sum(1 for a in result.applied if a.validation_passed is True)
    failed = sum(1 for a in result.applied if a.validation_passed is False)
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {failed}")
    lines.append(f"- Not run: {len(result.applied) - passed - failed}")

    return "\n".join(lines) + "\n"


def write_report(content: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path



# ---------------------------------------------------------------------------
# Cluster naming (context only — Claude Code supplies the names)
# ---------------------------------------------------------------------------

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
