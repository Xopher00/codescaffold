from __future__ import annotations

from pathlib import Path

import typer

from refactor_plan.applicator.apply import apply_plan
from refactor_plan.applicator.rollback import rollback as do_rollback
from refactor_plan.contracts.import_contracts import emit_contract
from refactor_plan.interface.cluster_view import build_view
from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.naming.namer import name_clusters, write_rename_map
from refactor_plan.planning.planner import RefactorPlan, plan as build_plan, write_plan
from refactor_plan.reporting.reporter import render_dry_run_report, write_report
from refactor_plan.validation.validator import validate as do_validate

app = typer.Typer(help="Graph-driven structural refactoring assistant.", no_args_is_help=True)


def _out_dir(repo_root: Path) -> Path:
    return repo_root / ".refactor_plan"


def _plan_path(repo_root: Path) -> Path:
    return _out_dir(repo_root) / "refactor_plan.json"


def _load_plan(repo_root: Path) -> RefactorPlan:
    path = _plan_path(repo_root)
    if not path.exists():
        typer.echo(f"No plan found at {path}. Run `analyze` first.", err=True)
        raise typer.Exit(code=1)
    return RefactorPlan.model_validate_json(path.read_text(encoding="utf-8"))


@app.command()
def analyze(
    repo: Path = typer.Argument(..., help="Path to target repository"),
    dry_run: bool = typer.Option(True, help="Print plan without applying"),
) -> None:
    """Extract graph, cluster, and plan file/symbol moves."""
    repo = repo.resolve()
    graph_path = ensure_graph(repo)
    view = build_view(graph_path)
    refactor_plan = build_plan(view, repo, graph_path)

    plan_dict = {
        "file_moves": [m.model_dump() for m in refactor_plan.file_moves],
        "symbol_moves": [m.model_dump() for m in refactor_plan.symbol_moves],
        "communities": [c.model_dump() for c in refactor_plan.clusters],
    }
    report_text = render_dry_run_report(plan_dict, str(repo))

    typer.echo(f"  {len(refactor_plan.file_moves)} file move(s), {len(refactor_plan.symbol_moves)} symbol move(s) proposed")

    if dry_run:
        typer.echo(report_text)
        typer.echo("Dry-run: plan not written. Pass --no-dry-run to save.")
        return

    out = _out_dir(repo)
    out.mkdir(parents=True, exist_ok=True)
    write_plan(refactor_plan, _plan_path(repo))
    report_path = write_report(report_text, out / "STRUCTURE_REPORT.md")
    typer.echo(f"Plan:   {_plan_path(repo)}")
    typer.echo(f"Report: {report_path}")


@app.command()
def apply(
    repo: Path = typer.Argument(..., help="Path to target repository"),
    dry_run: bool = typer.Option(True, help="Simulate without writing. Pass --no-dry-run to execute."),
) -> None:
    """Apply the refactor plan (file moves + symbol moves + import rewrites)."""
    repo = repo.resolve()
    refactor_plan = _load_plan(repo)
    out = _out_dir(repo)

    plan_dict = {
        "file_moves": [
            {"source": m.source, "dest": m.dest, "dest_package": m.dest_package}
            for m in refactor_plan.file_moves
        ],
        "symbol_moves": [
            {"source": m.source, "dest": m.dest, "symbol": m.symbol}
            for m in refactor_plan.symbol_moves
            if m.approved
        ],
    }
    result = apply_plan(plan_dict, repo, out, dry_run=dry_run)

    typer.echo(
        f"Applied: {len(result.applied)}  "
        f"Failed: {len(result.failed)}  "
        f"Skipped: {len(result.skipped)}"
    )
    for e in result.failed:
        typer.echo(f"  [{e.category}] {e.source}: {e.reason}", err=True)


@app.command()
def validate(
    repo: Path = typer.Argument(..., help="Path to target repository"),
) -> None:
    """Run validation commands; rollback on failure."""
    repo = repo.resolve()
    report = do_validate(repo)
    status = "PASSED" if report.passed else "FAILED"
    typer.echo(f"Validation {status}")
    for cmd_result in report.commands:
        mark = "OK" if cmd_result.exit_code == 0 else "FAIL"
        typer.echo(f"  [{mark}] {cmd_result.command}")
    if not report.passed:
        raise typer.Exit(code=1)


@app.command()
def rollback(
    repo: Path = typer.Argument(..., help="Path to target repository"),
) -> None:
    """Undo the last apply batch using manifest + rope history."""
    repo = repo.resolve()
    for action in do_rollback(repo, _out_dir(repo)):
        typer.echo(action)


@app.command()
def name(
    repo: Path = typer.Argument(..., help="Path to target repository"),
    model: str = typer.Option("claude-opus-4-7", help="Claude model to use"),
) -> None:
    """LLM naming pass: propose semantic names for pkg_NNN placeholders."""
    repo = repo.resolve()
    graph_path = ensure_graph(repo)
    view = build_view(graph_path)
    refactor_plan = _load_plan(repo)
    rename_map = name_clusters(refactor_plan, view, repo, graph_path, model=model)
    out_path = _out_dir(repo) / "rename_map.json"
    write_rename_map(rename_map, out_path)
    typer.echo(f"Rename map: {out_path}")
    for entry in rename_map.entries:
        typer.echo(f"  {entry.old_name} → {entry.new_name}")


@app.command()
def contracts(
    repo: Path = typer.Argument(..., help="Path to target repository"),
    root_package: str = typer.Option("refactor_plan", help="Root package for import-linter"),
) -> None:
    """Emit .importlinter contracts from current cluster structure."""
    repo = repo.resolve()
    graph_path = ensure_graph(repo)
    view = build_view(graph_path)
    refactor_plan = _load_plan(repo)
    artifact = emit_contract(refactor_plan, view, graph_path, repo, root_package=root_package)
    typer.echo(f".importlinter: {artifact.config_path}")
    typer.echo(f"  {len(artifact.contracts)} contract(s) emitted")
