from __future__ import annotations

from pathlib import Path

from refactor_plan.applicator.models import ApplyResult, MoveKind, MoveStrategy


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

    lines: list[str] = []
    lines.append("# Structure Report\n")

    lines.append("## Summary\n")
    lines.append(f"- Communities detected: {len(communities)}")
    lines.append(f"- File moves proposed: {len(file_moves)}")
    lines.append(f"- Symbol moves proposed: {len(symbol_moves)}")
    lines.append(f"- Repository root: `{repo_root}`\n")

    if file_moves:
        lines.append("## File Moves\n")
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
