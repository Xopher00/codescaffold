

# ---------------------------------------------------------------------------
# Structural analysis
# ---------------------------------------------------------------------------

from refactor_plan.contracts.import_contracts import generate_contracts as do_generate_contracts
from refactor_plan.planning.planner import plan as build_plan, write_plan
from refactor_plan.interface.cluster_view import build_view
from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.layout import detect_layout
from refactor_plan.reporting.reporter import render_dry_run_report, write_report
from refactor_plan.validation.validator import validate as do_validate
from refactor_plan.records.rollback import rollback as do_rollback
import subprocess
from refactor_plan.naming.docstringer import insert_docstring_text

@mcp.tool()
def analyze(repo: str = "") -> str:
    """Extract graph, cluster files, and produce a structural plan.

    Writes refactor_plan.json and STRUCTURE_REPORT.md under .refactor_plan/.
    Returns the structure report so you can review proposed moves.
    """
    root = _repo(repo)
    graph_path = ensure_graph(root)
    view = build_view(graph_path)
    plan = build_plan(view, root, graph_path)

    out = _out_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    write_plan(plan, _plan_path(root))

    plan_dict = {
        "file_moves": [m.model_dump() for m in plan.file_moves],
        "symbol_moves": [m.model_dump() for m in plan.symbol_moves],
        "communities": [c.model_dump() for c in plan.clusters],
        "pending_decisions": [d.model_dump() for d in plan.pending_decisions],
        "surprising_connections": view.surprising_connections,
        "god_nodes": view.god_nodes[:8],
    }
    report = render_dry_run_report(plan_dict, str(root))
    write_report(report, out / "STRUCTURE_REPORT.md")

    # Refresh contracts whenever the plan is regenerated
    layout = detect_layout(root)
    do_generate_contracts(plan, view, graph_path, root, layout, force=False)

    return report



@mcp.tool()
def validate(repo: str = "") -> str:
    """Run validation commands (compileall + pytest by default).

    Returns PASSED or FAILED with per-command results.
    """
    root = _repo(repo)
    report = do_validate(root)
    lines = ["PASSED" if report.passed else "FAILED"]
    for cmd in report.commands:
        mark = "OK" if cmd.exit_code == 0 else "FAIL"
        lines.append(f"  [{mark}] {cmd.command}")
        if cmd.exit_code != 0 and cmd.stderr:
            lines.append(cmd.stderr[:500])
    return "\n".join(lines)



@mcp.tool()
def rollback(repo: str = "") -> str:
    """Undo the last apply batch using the manifest and rope history."""
    root = _repo(repo)
    actions = do_rollback(root, _out_dir(root))
    return "\n".join(actions) if actions else "Nothing to roll back."



@mcp.tool()
def reset(repo: str = "") -> str:
    """Delete stale refactor plan, state, and import-linter contracts.

    Safe to call any time — removes .refactor_plan/refactor_plan.json,
    .refactor_plan/state.json, and .importlinter so the next analyze
    starts from a clean slate.  Does not touch manifests or reports.
    """
    root = _repo(repo)
    out_dir = _out_dir(root)
    _reset_stale_artifacts(out_dir)
    return (
        "Reset complete. Removed: refactor_plan.json, state.json, .importlinter "
        "(if present). Run analyze to start fresh."
    )



@mcp.tool()
def discard_sandbox(branch: str, repo: str = "") -> str:
    """Delete a sandbox branch without merging — discards the refactor."""
    root = _repo(repo)
    result = subprocess.run(
        ["git", "-C", str(root), "branch", "-D", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"FAILED: {result.stderr.strip()}"
    return f"Branch '{branch}' deleted."



@mcp.tool()
def insert_docstring(target: str, docstring_text: str, repo: str = "") -> str:
    """Insert or replace the docstring for a symbol.

    target format: 'src/pkg/mod.py::SymbolName'
    docstring_text — plain text only, no quotes.

    Uses LibCST for syntax-preserving insertion.
    """
    root = _repo(repo)

    if "::" not in target:
        return "target must be in the form 'path/to/file.py::SymbolName'"

    file_part, symbol_name = target.split("::", 1)
    file_path = (root / file_part).resolve()

    error = insert_docstring_text(file_path, symbol_name, docstring_text)
    if error:
        return f"FAILED: {error}"
    return f"Docstring written for '{symbol_name}' in {file_path.relative_to(root)}"



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()
