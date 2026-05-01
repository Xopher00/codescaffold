"""CLI entry point for graph-driven structural refactoring: dry-run commands.

Provides four subcommands:
  - extract: Run graphify to extract the code graph
  - plan: Generate a RefactorPlan from the graph
  - report: Render a dry-run report
  - run: Chain extract → plan → report
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from refactor_plan.cluster_view import build_view
from refactor_plan.planner import plan as build_plan
from refactor_plan.planner import RefactorPlan
from refactor_plan.reporter import render_dry_run_report_text

app = typer.Typer(
    help="Graph-driven structural refactoring assistant — dry-run commands."
)


@app.command()
def extract(
    repo: Path = typer.Argument(..., help="Path to repository"),
    force: bool = typer.Option(
        False, "--force", help="Regenerate graph.json even if it exists"
    ),
) -> None:
    """Extract code graph using graphify."""
    refactor_dir = repo / ".refactor_plan"
    graph_path = refactor_dir / "graph.json"

    if graph_path.exists() and not force:
        typer.echo("graph.json already present, skipping (use --force to regenerate)")
        return

    # Run graphify update command
    result = subprocess.run(
        ["graphify", "update", str(repo)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        typer.echo(result.stderr, err=True)
        raise typer.Exit(code=1)

    # Copy from graphify-out to .refactor_plan
    graphify_out_graph = repo / "graphify-out" / "graph.json"
    if not graphify_out_graph.exists():
        typer.echo(
            "error: graphify did not produce graphify-out/graph.json",
            err=True,
        )
        raise typer.Exit(code=1)

    refactor_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(graphify_out_graph), str(graph_path))

    typer.echo("extracted graph → .refactor_plan/graph.json")


@app.command()
def plan(repo: Path = typer.Argument(..., help="Path to repository")) -> None:
    """Generate a RefactorPlan from the extracted graph."""
    graph_path = repo / ".refactor_plan" / "graph.json"

    if not graph_path.exists():
        typer.echo(
            "missing .refactor_plan/graph.json — run `refactor-plan extract` first",
            err=True,
        )
        raise typer.Exit(code=1)

    # Build view and plan
    view = build_view(graph_path)
    p = build_plan(view, repo, graph_path)

    # Write plan
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(p.model_dump_json(indent=2))

    typer.echo(
        f"wrote refactor_plan.json ({len(p.file_moves)} file_moves, "
        f"{len(p.symbol_moves)} symbol_moves, {len(p.shim_candidates)} shim_candidates, "
        f"{len(p.splitting_candidates)} splitting_candidates)"
    )


@app.command()
def report(repo: Path = typer.Argument(..., help="Path to repository")) -> None:
    """Render a dry-run report."""
    graph_path = repo / ".refactor_plan" / "graph.json"
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"

    if not graph_path.exists():
        typer.echo(
            "missing .refactor_plan/graph.json — run `refactor-plan extract` first",
            err=True,
        )
        raise typer.Exit(code=1)

    if not plan_path.exists():
        typer.echo(
            "missing .refactor_plan/refactor_plan.json — run `refactor-plan plan` first",
            err=True,
        )
        raise typer.Exit(code=1)

    # Reload view and plan
    view = build_view(graph_path)
    plan_obj = RefactorPlan.model_validate_json(plan_path.read_text())

    # Render report
    text = render_dry_run_report_text(view, plan_obj)

    # Write report
    report_path = repo / ".refactor_plan" / "STRUCTURE_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text)

    typer.echo(f"wrote STRUCTURE_REPORT.md ({len(text)} chars)")


@app.command()
def run(
    repo: Path = typer.Argument(..., help="Path to repository"),
    force: bool = typer.Option(
        False, "--force", help="Regenerate graph.json even if it exists"
    ),
) -> None:
    """Run the full pipeline: extract → plan → report."""
    extract(repo, force)
    plan(repo)
    report(repo)


if __name__ == "__main__":
    app()
