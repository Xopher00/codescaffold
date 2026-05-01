"""Reporter: compose graphify report + delta header into STRUCTURE_REPORT.md.

This module is responsible for rendering:
1. Dry-run reports: delta header + cluster summary + plan tables (no graphify.report.generate).
2. Apply reports: delta header + graphify.report.generate(pre/post) + graph_diff + validation.

The key principle is that we use graphify's own report generator for the structural pages
and only provide additional delta sections for our refactoring-specific data.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import graphify.analyze as ganalyze
import graphify.cluster as gcluster
import graphify.report as greport

from refactor_plan.cleaner import DeadCodeReport
from refactor_plan.cluster_view import GraphView, load_graph
from refactor_plan.planner import RefactorPlan


def _format_header(plan: RefactorPlan, view: GraphView) -> str:
    """Format the title and summary header section."""
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    file_moves_count = len(plan.file_moves)
    symbol_moves_count = len(plan.symbol_moves)
    symbol_moves_approved = sum(1 for s in plan.symbol_moves if s.approved)
    shim_count = len(plan.shim_candidates)
    splitting_count = len(plan.splitting_candidates)

    return f"""# STRUCTURE_REPORT

**Generated:** {date_str}

## Summary

- **Clusters detected:** {len(plan.clusters)}
- **File moves proposed:** {file_moves_count}
- **Symbol moves proposed:** {symbol_moves_count} ({symbol_moves_approved} approved)
- **Shim candidates:** {shim_count}
- **Splitting candidates:** {splitting_count}
- **God nodes:** {len(view.god_nodes)}
- **Surprising connections:** {len(view.surprising_connections)}

"""


def _format_clusters_table(plan: RefactorPlan) -> str:
    """Format clusters as a markdown table."""
    lines = ["## Clusters", ""]
    lines.append("| Package | Community ID | Files | Cohesion |")
    lines.append("|---------|--------------|-------|----------|")

    for cluster in plan.clusters:
        lines.append(
            f"| {cluster.name} | {cluster.community_id} | {len(cluster.files)} | {cluster.cohesion:.3f} |"
        )

    lines.append("")
    return "\n".join(lines)


def _format_file_moves_table(plan: RefactorPlan) -> str:
    """Format file moves as a markdown table."""
    lines = ["## File moves", ""]

    if not plan.file_moves:
        lines.append("(no file moves)")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Source | Destination | Cluster | Cohesion |")
    lines.append("|--------|-------------|---------|----------|")

    for move in plan.file_moves:
        lines.append(
            f"| {move.src} | {move.dest} | {move.cluster} | {move.cohesion:.3f} |"
        )

    lines.append("")
    return "\n".join(lines)


def _format_symbol_moves_table(plan: RefactorPlan) -> str:
    """Format symbol moves as a markdown table."""
    lines = ["## Symbol moves", ""]

    if not plan.symbol_moves:
        lines.append("(no symbol moves)")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Symbol | Source file | Dest cluster | Approved |")
    lines.append("|--------|-------------|--------------|----------|")

    for move in plan.symbol_moves:
        approved_str = "✓" if move.approved else "✗"
        lines.append(
            f"| {move.label} | {move.src_file} | {move.dest_cluster} | {approved_str} |"
        )

    lines.append("")
    return "\n".join(lines)


def _format_shim_candidates_table(plan: RefactorPlan) -> str:
    """Format shim candidates as a markdown table."""
    lines = ["## Shim candidates", ""]

    if not plan.shim_candidates:
        lines.append("(no shim candidates)")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Source | Triggers |")
    lines.append("|--------|----------|")

    for candidate in plan.shim_candidates:
        triggers_str = ", ".join(candidate.triggers)
        lines.append(f"| {candidate.src} | {triggers_str} |")

    lines.append("")
    return "\n".join(lines)


def _format_splitting_candidates(plan: RefactorPlan) -> str:
    """Format splitting candidates as a bulleted list."""
    lines = ["## Splitting candidates", ""]

    if not plan.splitting_candidates:
        lines.append("(no splitting candidates)")
        lines.append("")
        return "\n".join(lines)

    for candidate in plan.splitting_candidates:
        lines.append(f"- **{candidate.type}**: {candidate.question}")
        lines.append(f"  - Why: {candidate.why}")

    lines.append("")
    return "\n".join(lines)


def _format_god_nodes(view: GraphView) -> str:
    """Format top god nodes as a bulleted list."""
    lines = ["## God nodes (high edge count)", ""]

    if not view.god_nodes:
        lines.append("(no god nodes detected)")
        lines.append("")
        return "\n".join(lines)

    for node in view.god_nodes[:5]:
        label = node.get("label", node.get("id", "unknown"))
        edges = node.get("edges", 0)
        lines.append(f"- `{label}`: {edges} edges")

    lines.append("")
    return "\n".join(lines)


def _format_surprising_connections(view: GraphView) -> str:
    """Format surprising connections as a bulleted list."""
    lines = ["## Surprising connections (cross-community edges)", ""]

    if not view.surprising_connections:
        lines.append("(no surprising connections detected)")
        lines.append("")
        return "\n".join(lines)

    for conn in view.surprising_connections[:10]:
        question = conn.get("question", "unknown")
        why = conn.get("why", "")
        lines.append(f"- {question}")
        if why:
            lines.append(f"  - Why: {why}")

    lines.append("")
    return "\n".join(lines)


def _format_suggested_questions(view: GraphView) -> str:
    """Format suggested questions as a bulleted list."""
    lines = ["## Suggested questions", ""]

    if not view.suggested_questions:
        lines.append("(no suggested questions)")
        lines.append("")
        return "\n".join(lines)

    for question in view.suggested_questions:
        q_text = question.get("question", "unknown")
        lines.append(f"- {q_text}")

    lines.append("")
    return "\n".join(lines)


def _recover_communities_for_graphify(G) -> dict[int, list[str]]:
    """Recover communities dict from per-node community attribute."""
    communities: dict[int, list[str]] = {}
    for n, d in G.nodes(data=True):
        cid = d.get("community")
        if cid is not None:
            communities.setdefault(cid, []).append(n)
    return communities


def _make_detection_result_stub() -> dict:
    """Create a minimal stub detection_result for graphify.report.generate."""
    return {
        "total_files": 0,
        "total_words": 0,
        "warning": None,
        "files": {"code": []},
    }


def _make_token_cost_stub() -> dict:
    """Create a minimal stub token_cost for graphify.report.generate."""
    return {"input": 0, "output": 0}


def render_dry_run_report_text(view: GraphView, plan: RefactorPlan) -> str:
    """Dry-run: delta header + cluster summary + plan tables. Returns text (no write).

    Args:
        view: The GraphView containing structural analysis.
        plan: The RefactorPlan to render.

    Returns:
        The rendered report as a string.
    """
    sections = [
        _format_header(plan, view),
        _format_clusters_table(plan),
        _format_file_moves_table(plan),
        _format_symbol_moves_table(plan),
        _format_shim_candidates_table(plan),
        _format_splitting_candidates(plan),
        _format_god_nodes(view),
        _format_surprising_connections(view),
        _format_suggested_questions(view),
    ]

    return "\n".join(sections)


def render_dry_run_report(
    plan: RefactorPlan,
    view: GraphView,
    output_path: Path,
) -> None:
    """Dry-run: delta header + cluster summary + plan tables."""
    report = render_dry_run_report_text(view, plan)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")


def render_apply_report(
    plan: RefactorPlan,
    pre_view: GraphView,
    post_view: GraphView,
    pre_graph_path: Path,
    post_graph_path: Path,
    manifest: dict,
    validation: dict | None,
    output_path: Path,
    *,
    repo_root: Path,
) -> None:
    """Apply: delta header + graphify.report.generate(pre/post) + graph_diff + validation.

    Args:
        plan: The RefactorPlan that was applied.
        pre_view: GraphView before applying the plan.
        post_view: GraphView after applying the plan.
        pre_graph_path: Path to pre-apply graph.json.
        post_graph_path: Path to post-apply graph.json.
        manifest: Dictionary of applied changes (file moves, symbol moves, shims, etc.).
        validation: Dictionary of validation results (can be None).
        output_path: Path where to write the report.
        repo_root: Repository root for graphify.report.generate.
    """
    # Load the actual graph objects for graphify.report.generate
    pre_G = load_graph(pre_graph_path)
    post_G = load_graph(post_graph_path)

    # Recover communities for graphify
    pre_communities = _recover_communities_for_graphify(pre_G)
    post_communities = _recover_communities_for_graphify(post_G)

    # Compute cohesion scores
    pre_cohesion = gcluster.score_all(pre_G, pre_communities)
    post_cohesion = gcluster.score_all(post_G, post_communities)

    # Create community labels
    pre_labels = {cid: f"pkg_{cid:03d}" for cid in pre_communities}
    post_labels = {cid: f"pkg_{cid:03d}" for cid in post_communities}

    # Get graphify analyses
    pre_god_nodes = ganalyze.god_nodes(pre_G, top_n=10)
    post_god_nodes = ganalyze.god_nodes(post_G, top_n=10)

    pre_surprising = ganalyze.surprising_connections(pre_G, pre_communities, top_n=20)
    post_surprising = ganalyze.surprising_connections(post_G, post_communities, top_n=20)

    pre_questions = ganalyze.suggest_questions(pre_G, pre_communities, pre_labels)
    post_questions = ganalyze.suggest_questions(post_G, post_communities, post_labels)

    # Stubs for graphify
    detection_stub = _make_detection_result_stub()
    token_stub = _make_token_cost_stub()

    # Generate graphify reports
    pre_report = greport.generate(
        pre_G,
        pre_communities,
        pre_cohesion,
        pre_labels,
        pre_god_nodes,
        pre_surprising,
        detection_stub,
        token_stub,
        str(repo_root),
        suggested_questions=pre_questions,
    )

    post_report = greport.generate(
        post_G,
        post_communities,
        post_cohesion,
        post_labels,
        post_god_nodes,
        post_surprising,
        detection_stub,
        token_stub,
        str(repo_root),
        suggested_questions=post_questions,
    )

    # Generate graph diff
    try:
        graph_diff = ganalyze.graph_diff(pre_G, post_G)
        graph_diff_str = f"## Graph diff\n\n```\n{graph_diff}\n```\n\n"
    except Exception:
        graph_diff_str = "## Graph diff\n\n(Unable to compute graph diff)\n\n"

    # Format validation results
    validation_str = ""
    if validation:
        validation_str = "## Validation results\n\n"
        if isinstance(validation, dict):
            for key, value in validation.items():
                validation_str += f"- **{key}**: {value}\n"
        validation_str += "\n"

    # Format manifest
    manifest_str = "## Manifest\n\n"
    if isinstance(manifest, dict):
        file_moves_applied = manifest.get("file_moves_applied", 0)
        symbol_moves_applied = manifest.get("symbol_moves_applied", 0)
        shims_created = manifest.get("shims_created", 0)
        imports_organized = manifest.get("imports_organized", 0)

        manifest_str += f"- File moves applied: {file_moves_applied}\n"
        manifest_str += f"- Symbol moves applied: {symbol_moves_applied}\n"
        manifest_str += f"- Shims created: {shims_created}\n"
        manifest_str += f"- Imports organized: {imports_organized}\n"
    manifest_str += "\n"

    # Format before/after god_nodes section
    def _format_god_nodes_before_after(pre: GraphView, post: GraphView) -> str:
        lines = ["## God nodes (high edge count)", ""]
        lines.append("### Before")
        lines.append("")
        if pre.god_nodes:
            for node in pre.god_nodes[:5]:
                label = node.get("label", node.get("id", "unknown"))
                edges = node.get("edges", 0)
                lines.append(f"- `{label}`: {edges} edges")
        else:
            lines.append("(no god nodes detected)")
        lines.append("")
        lines.append("### After")
        lines.append("")
        if post.god_nodes:
            for node in post.god_nodes[:5]:
                label = node.get("label", node.get("id", "unknown"))
                edges = node.get("edges", 0)
                lines.append(f"- `{label}`: {edges} edges")
        else:
            lines.append("(no god nodes detected)")
        lines.append("")
        return "\n".join(lines)

    def _format_surprising_connections_before_after(pre: GraphView, post: GraphView) -> str:
        lines = ["## Surprising connections (cross-community edges)", ""]
        lines.append("### Before")
        lines.append("")
        if pre.surprising_connections:
            for conn in pre.surprising_connections[:10]:
                question = conn.get("question", "unknown")
                why = conn.get("why", "")
                lines.append(f"- {question}")
                if why:
                    lines.append(f"  - Why: {why}")
        else:
            lines.append("(no surprising connections detected)")
        lines.append("")
        lines.append("### After")
        lines.append("")
        if post.surprising_connections:
            for conn in post.surprising_connections[:10]:
                question = conn.get("question", "unknown")
                why = conn.get("why", "")
                lines.append(f"- {question}")
                if why:
                    lines.append(f"  - Why: {why}")
        else:
            lines.append("(no surprising connections detected)")
        lines.append("")
        return "\n".join(lines)

    def _format_suggested_questions_before_after(pre: GraphView, post: GraphView) -> str:
        lines = ["## Suggested questions", ""]
        lines.append("### Before")
        lines.append("")
        if pre.suggested_questions:
            for question in pre.suggested_questions:
                q_text = question.get("question", "unknown")
                lines.append(f"- {q_text}")
        else:
            lines.append("(no suggested questions)")
        lines.append("")
        lines.append("### After")
        lines.append("")
        if post.suggested_questions:
            for question in post.suggested_questions:
                q_text = question.get("question", "unknown")
                lines.append(f"- {q_text}")
        else:
            lines.append("(no suggested questions)")
        lines.append("")
        return "\n".join(lines)

    # Assemble full report
    sections = [
        _format_header(plan, pre_view),
        _format_clusters_table(plan),
        _format_file_moves_table(plan),
        _format_symbol_moves_table(plan),
        _format_shim_candidates_table(plan),
        _format_splitting_candidates(plan),
        _format_god_nodes_before_after(pre_view, post_view),
        _format_surprising_connections_before_after(pre_view, post_view),
        _format_suggested_questions_before_after(pre_view, post_view),
        "\n## Pre-apply graphify report\n\n",
        pre_report,
        "\n## Post-apply graphify report\n\n",
        post_report,
        graph_diff_str,
        validation_str,
        manifest_str,
    ]

    report = "\n".join(str(s) for s in sections)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")


def render_dead_code_report_md(report: DeadCodeReport) -> str:
    """Render a DeadCodeReport as a markdown table (DEAD_CODE_REPORT.md).

    Approvals remain in the JSON; this function is output-only.
    The Approved column shows `[x]` if entry.approved is True, else `[ ]`.
    """
    lines = ["# DEAD_CODE_REPORT", ""]
    lines.append("| Label | Source file | Source location | Rationale | Approved |")
    lines.append("|-------|-------------|-----------------|-----------|----------|")

    for sym in report.symbols:
        approved_str = "[x]" if sym.approved else "[ ]"
        # Embed edge_context into rationale column for clarity
        rationale_col = f"{sym.rationale} ({sym.edge_context})"
        lines.append(
            f"| {sym.label} | {sym.source_file} | {sym.source_location} "
            f"| {rationale_col} | {approved_str} |"
        )

    if not report.symbols:
        lines.append("| (none) | | | | |")

    lines.append("")
    return "\n".join(lines)
