from __future__ import annotations

from pathlib import Path
from refactor_plan.applicator.execution.models import ApplyResult, MoveKind, MoveStrategy


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
            status = "PLACEMENT NEEDED" if d.get("needs_placement") else "confirmed"
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
