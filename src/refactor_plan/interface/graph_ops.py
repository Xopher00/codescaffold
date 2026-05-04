

import subprocess

from refactor_plan.contracts.import_contracts import check_staleness, generate_contracts as do_generate_contracts
from .cluster_view import build_view
from .graph_bridge import ensure_graph
from refactor_plan.layout import detect_layout
from refactor_plan.naming.docstringer import build_docstring_context
from refactor_plan.naming.namer import build_naming_context
from refactor_plan.reporting.cluster_context import _format_pending_decisions
from refactor_plan.server_helpers import _load_plan, _repo


def contracts(repo: str = "", force: bool = False) -> str:
    """Generate or refresh .importlinter contracts from current graph structure.

    Derives three contract types using grimp (real Python imports, not inferred):
    - layers: topological ordering of packages by import direction (skipped if cycles exist)
    - independence: packages with no imports between any pair
    - forbidden: unexpected cross-cluster dependencies from surprising connections

    Writes .importlinter at repo root with a provenance header.
    Will NOT overwrite hand-edited files unless force=True.
    Re-run after apply or apply_rename_map to refresh.
    """
    root = _repo(repo)
    graph_path = ensure_graph(root)
    view = build_view(graph_path)
    plan = _load_plan(root)
    layout = detect_layout(root)

    artifact = do_generate_contracts(plan, view, graph_path, root, layout, force=force)

    if artifact.skipped_reason:
        return f"SKIPPED: {artifact.skipped_reason}"

    lines = [f"Generated {len(artifact.contracts)} contract(s) → {artifact.config_path}"]
    for spec in artifact.contracts:
        if spec.contract_type == "independence":
            lines.append(f"  independence: {', '.join(spec.modules)}")
        elif spec.contract_type == "layers":
            lines.append(f"  layers: {len(spec.layers)} levels")
        elif spec.contract_type == "forbidden":
            lines.append(f"  forbidden: {', '.join(spec.source_modules)} ✗→ {', '.join(spec.forbidden_modules)}")

    if artifact.cycles_detected:
        lines.append(f"\n  WARNING: {len(artifact.cycles_detected)} import cycle(s) detected — layers contract skipped.")
        for s in artifact.cycle_break_suggestions[:5]:
            lines.append(f"\n  Cycle {' → '.join(s.cycle + [s.cycle[0]])}")
            lines.append(f"    Edge   : {s.edge}")
            lines.append(f"    Cause  : {s.cause}")
            lines.append(f"    Fix    : {s.suggestion}")

    lines.append(f"\nGraph mtime: {artifact.graph_mtime_iso}")
    lines.append("Run validate_contracts to check compliance.")
    return "\n".join(lines)




def validate_contracts(repo: str = "") -> str:
    """Run import-linter to check .importlinter contracts.

    Returns PASSED or FAILED with per-contract results.
    Warns if contracts may be stale (graph changed since last generate_contracts).
    Run contracts first if no .importlinter file exists.
    """
    root = _repo(repo)
    config_path = root / ".importlinter"
    graph_path = ensure_graph(root)

    if not config_path.exists():
        return "No .importlinter found — run contracts first."

    is_stale, staleness_reason = check_staleness(config_path, graph_path)

    result = subprocess.run(
        ["python", "-m", "importlinter", "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=str(root),
    )

    status = "PASSED" if result.returncode == 0 else "FAILED"
    lines = [status]
    if is_stale:
        lines.append(f"  WARNING: {staleness_reason}")
    if result.stdout:
        lines.append(result.stdout[:1500])
    if result.returncode != 0 and result.stderr:
        lines.append(result.stderr[:500])
    return "\n".join(lines)




def get_cluster_context(repo: str = "") -> str:
    """Return structured context for each placeholder cluster.

    The current directory layout reflects history, not architecture. Do not treat
    co-location as evidence of correct placement — use graph signals (cohesion,
    dependency direction, surprising connections) to evaluate each community.

    Output is structured as:
      1. Action list — which communities need placement decisions, review, or nothing.
         Read this first to scope your work.
      2. Detail blocks — one per community that needs a decision or review.
         No-action communities are omitted from detail.

    Cohesion guide:
      < 0.10  almost no structural coupling — files may share a folder by accident
      0.10–0.20  weak coupling — review before treating as placement-stable
      0.20–0.40  moderate coupling
      > 0.40  strong internal dependencies — likely correctly placed

    File role signals: hub (god node), bridge (surprising cross-community connections),
    leaf (mostly consumed, few outgoing), isolated (very few connections).

    After reviewing, call:
      approve_moves([{"source": "...", "dest": "..."}]) for placement decisions
      apply_rename_map({"pkg_001": "auth", ...}) for naming decisions
    """
    root = _repo(repo)
    graph_path = ensure_graph(root)
    view = build_view(graph_path)
    plan = _load_plan(root)

    sections: list[str] = []

    decisions_section = _format_pending_decisions(plan, root, view)
    if decisions_section:
        sections.append(decisions_section)

    naming_context = build_naming_context(plan, view)
    if naming_context:
        sections.append(naming_context)

    if not sections:
        return "No pending decisions or placeholder clusters found in the current plan."

    return "\n\n".join(sections)



# ---------------------------------------------------------------------------
# Docstring generation (context only — Claude Code writes the text)
# ---------------------------------------------------------------------------


def get_symbol_context(target: str, repo: str = "") -> str:
    """Return graph context for a symbol to help write its docstring.

    target format: 'src/pkg/mod.py::SymbolName'

    Returns the symbol's methods, callers, callees, and existing docstring.
    Use this context to write a concise one-sentence docstring, then call
    insert_docstring with the text.
    """
    root = _repo(repo)

    if "::" not in target:
        return "target must be in the form 'path/to/file.py::SymbolName'"

    file_part, symbol_name = target.split("::", 1)
    file_path = (root / file_part).resolve()
    graph_path = ensure_graph(root)
    view = build_view(graph_path)

    context = build_docstring_context(file_path, symbol_name, view)
    if context is None:
        return f"Symbol '{symbol_name}' not found in {file_path.name}"

    return context + "\n\nWrite a concise one-sentence docstring, then call insert_docstring."
