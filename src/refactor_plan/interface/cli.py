from __future__ import annotations

from pathlib import Path

import typer

from refactor_plan.applicator.apply import apply_plan
from refactor_plan.applicator.manifests import write_manifest
from refactor_plan.applicator.models import ApplyResult, Escalation
from refactor_plan.applicator.name_apply import apply_rename_map
from refactor_plan.applicator.rollback import rollback as do_rollback
from refactor_plan.applicator.rope_rename import rename_module, rename_symbol
from refactor_plan.contracts.import_contracts import emit_contract
from refactor_plan.interface.cluster_view import build_view
from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.naming.namer import RenameMap
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


@app.command(name="apply-names")
def apply_names(
    repo: Path = typer.Argument(..., help="Path to target repository"),
    dry_run: bool = typer.Option(True, help="Show what would be renamed. Pass --no-dry-run to execute."),
) -> None:
    """Apply rename_map.json: rename pkg_NNN packages to semantic names."""
    repo = repo.resolve()
    refactor_plan = _load_plan(repo)

    rename_map_path = _out_dir(repo) / "rename_map.json"
    if not rename_map_path.exists():
        typer.echo(f"No rename map at {rename_map_path}. Run `name` first.", err=True)
        raise typer.Exit(code=1)
    rename_map = RenameMap.model_validate_json(rename_map_path.read_text(encoding="utf-8"))

    if not rename_map.entries:
        typer.echo("Rename map is empty — nothing to apply.")
        return

    result = apply_rename_map(rename_map, refactor_plan, repo, _out_dir(repo), dry_run=dry_run)

    if dry_run:
        typer.echo(f"Would rename {len(result.applied)} package(s):")
        for action in result.applied:
            src_name = Path(action.source).name
            dest_name = Path(action.dest).name
            typer.echo(f"  {src_name} → {dest_name}")
        if result.skipped:
            typer.echo(f"  {len(result.skipped)} skipped (directory missing)")
        if result.failed:
            for e in result.failed:
                typer.echo(f"  [skip] {e.source}: {e.reason}", err=True)
        typer.echo("Dry-run complete. Pass --no-dry-run to execute.")
        return

    typer.echo(
        f"Renamed: {len(result.applied)}  "
        f"Failed: {len(result.failed)}  "
        f"Skipped: {len(result.skipped)}"
    )
    for action in result.applied:
        src_name = Path(action.source).name
        dest_name = Path(action.dest).name
        strat = action.strategy.value if action.strategy else "?"
        typer.echo(f"  {src_name} → {dest_name}  [{strat}, {action.imports_rewritten} import(s) rewritten]")
    for e in result.failed:
        typer.echo(f"  [FAIL] {e.source}: {e.reason}", err=True)


@app.command()
def rename(
    repo: Path = typer.Argument(..., help="Path to target repository"),
    target: str = typer.Argument(
        ...,
        help=(
            "What to rename. Formats: "
            "'src/pkg/mod.py::MyFunc' (symbol), "
            "'src/pkg/mod.py' (module file), "
            "'src/pkg/' (package directory)."
        ),
    ),
    to: str = typer.Option(..., "--to", help="New name (simple identifier, no path)"),
    dry_run: bool = typer.Option(True, help="Preview changes. Pass --no-dry-run to apply."),
) -> None:
    """Rename a symbol, module, or package — propagates to all call sites."""
    repo = repo.resolve()

    if "::" in target:
        file_part, symbol_name = target.split("::", 1)
        file_path = (repo / file_part).resolve()
        action = rename_symbol(repo, file_path, symbol_name, to, dry_run=dry_run)
    else:
        module_path = (repo / target).resolve()
        action = rename_module(repo, module_path, to, dry_run=dry_run)

    if isinstance(action, Escalation):
        typer.echo(f"[FAIL] {action.reason}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo(f"Would rename '{target}' → '{to}'")
        typer.echo(f"  Files that would change: {len(action.files_touched)}")
        for f in sorted(action.files_touched):
            typer.echo(f"    {f}")
        typer.echo("Dry-run complete. Pass --no-dry-run to apply.")
        return

    typer.echo(f"Renamed '{target}' → '{to}'")
    typer.echo(f"  Strategy: {action.strategy.value if action.strategy else '?'}")
    typer.echo(f"  Files touched: {len(action.files_touched)}")
    typer.echo(f"  Imports rewritten: {action.imports_rewritten}")

    out_dir = _out_dir(repo)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(ApplyResult(applied=[action]), out_dir)


