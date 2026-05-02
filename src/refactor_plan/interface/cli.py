"""CLI entry point for graph-driven structural refactoring."""

from __future__ import annotations

from pathlib import Path

import typer
from rope.base import libutils
from rope.base.project import Project
from rope.refactor.rename import Rename

from refactor_plan.interface.cluster_view import build_view, load_graph
from refactor_plan.interface.graph_bridge import ensure_graph, normalize_source_files
from refactor_plan.entropy.cleaner import (
    DeadCodeReport,
    apply_dead_code_report,
    build_dead_code_report,
    load_dead_code_report,
    save_dead_code_report,
)
from refactor_plan.entropy.splitter import SplitPlan, apply_split_plan, build_split_plan
from refactor_plan.planning.planner import RefactorPlan, plan as build_plan, write_plan
from refactor_plan.reporting.reporter import render_dead_code_report_md, render_dry_run_report
from refactor_plan.applicator.rope_runner import (
    AppliedAction,
    ApplyResult,
    Escalation,
    apply_plan,
    rollback,
)
from refactor_plan.validation.validator import validate
from refactor_plan.naming.namer import RenameMap, name_clusters as propose_rename_map, write_rename_map


app = typer.Typer(
    help="Graph-driven structural refactoring assistant.",
    no_args_is_help=True,
)

_CONFIRM_DELETE_MESSAGE = (
    "use --apply --confirmed to execute deletions; edit dead_code_report.json "
    "to set approved=True first"
)


def _refactor_dir(repo_root: Path) -> Path:
    return repo_root / ".refactor_plan"


def _graph_path(repo_root: Path) -> Path:
    return _refactor_dir(repo_root) / "graph.json"


def _plan_path(repo_root: Path) -> Path:
    return _refactor_dir(repo_root) / "refactor_plan.json"


def _split_plan_path(repo_root: Path) -> Path:
    return _refactor_dir(repo_root) / "split_plan.json"


def _rename_map_path(repo_root: Path) -> Path:
    return _refactor_dir(repo_root) / "rename_map.json"



def _load_plan(repo_root: Path) -> RefactorPlan:
    path = _plan_path(repo_root)
    if not path.exists():
        typer.echo("missing .refactor_plan/refactor_plan.json", err=True)
        raise typer.Exit(code=1)
    return RefactorPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _save_plan(refactor_plan: RefactorPlan, repo_root: Path) -> None:
    write_plan(refactor_plan, _plan_path(repo_root))


def _write_split_plan(plan: SplitPlan, repo_root: Path) -> Path:
    path = _split_plan_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return path


def _load_split_plan(repo_root: Path) -> SplitPlan:
    path = _split_plan_path(repo_root)
    return SplitPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _approve_symbol_moves(
    refactor_plan: RefactorPlan,
    *,
    approve_all: bool,
    review: bool,
) -> int:
    approved = 0
    for move in refactor_plan.symbol_moves:
        if move.approved:
            approved += 1
            continue
        if approve_all:
            move.approved = True
        elif review:
            typer.echo(
                f"{move.label}: {move.src_file} -> {move.dest_file} "
                f"({move.dest_cluster})"
            )
            move.approved = typer.confirm("Approve this symbol move?", default=False)
        if move.approved:
            approved += 1
    return approved


def _approve_split_moves(
    split_plan: SplitPlan,
    *,
    approve_all: bool,
    review: bool,
) -> int:
    approved = 0
    for split in split_plan.splits:
        if split.approved:
            approved += 1
            continue
        if approve_all:
            split.approved = True
        elif review:
            typer.echo(
                f"{split.label}: {split.source_file} -> "
                f"{split.dest_pkg}/{split.dest_mod}"
            )
            typer.echo(f"  {split.rationale}")
            split.approved = typer.confirm("Approve this split move?", default=False)
        if split.approved:
            approved += 1
    return approved


def _approve_dead_symbols(
    report: DeadCodeReport,
    *,
    approve_all: bool,
    review: bool,
) -> int:
    approved = 0
    for symbol in report.symbols:
        if symbol.approved:
            approved += 1
            continue
        if approve_all:
            symbol.approved = True
        elif review:
            typer.echo(
                f"{symbol.label}: {symbol.source_file}:{symbol.source_location}"
            )
            typer.echo(f"  {symbol.rationale} ({symbol.edge_context})")
            symbol.approved = typer.confirm("Approve this deletion?", default=False)
        if symbol.approved:
            approved += 1
    return approved


def _applied_count(result: ApplyResult) -> int:
    return len([a for a in result.applied if a.history_index != -1])


def _validate_or_exit(
    repo_root: Path,
    result: ApplyResult,
    *,
    cleanup_paths: list[Path] | None = None,
) -> None:
    applied_count = _applied_count(result)
    report = validate(
        repo_root,
        applied_count,
        escalations=result.escalations,
        cleanup_paths=cleanup_paths,
    )
    if report.passed:
        return

    if not getattr(report, "rolled_back", False):
        rollback(repo_root, applied_count)
    typer.echo("validation failed; rolled back applied changes", err=True)
    raise typer.Exit(code=1)


def _symbol_name(label: str) -> str:
    return label.rstrip("()").lstrip(".")


def _rename_entry(repo_root: Path, project: Project, old_name: str, new_name: str) -> AppliedAction:
    old_path = repo_root / old_name.replace(".", "/")
    new_leaf = new_name.rsplit(".", 1)[-1].replace("-", "_")

    if old_path.is_dir():
        resource = libutils.path_to_resource(project, str(old_path), type="folder")
        if resource is None:
            raise FileNotFoundError(old_name)
        changes = Rename(project, resource).get_changes(new_leaf)
        project.do(changes)
        return AppliedAction(
            kind="rename",
            description=f"Renamed {old_name} -> {new_leaf}",
            history_index=len(project.history.undo_list),
        )

    module_path = old_path.with_suffix(".py")
    if module_path.exists():
        resource = libutils.path_to_resource(project, str(module_path))
        if resource is None:
            raise FileNotFoundError(old_name)
        changes = Rename(project, resource).get_changes(new_leaf)
        project.do(changes)
        return AppliedAction(
            kind="rename",
            description=f"Renamed {old_name} -> {new_leaf}",
            history_index=len(project.history.undo_list),
        )

    module_name, _, symbol = old_name.rpartition(".")
    if not module_name:
        raise FileNotFoundError(old_name)
    source_path = repo_root / module_name.replace(".", "/")
    source_path = source_path.with_suffix(".py")
    if not source_path.exists():
        raise FileNotFoundError(old_name)

    resource = libutils.path_to_resource(project, str(source_path))
    if resource is None:
        raise FileNotFoundError(old_name)

    from refactor_plan.applicator.rope_runner import _preflight_file

    offset_map, escalations = _preflight_file(
        source_path,
        [(old_name, f"{symbol}()"), (old_name, symbol)],
    )
    offset = offset_map.get((str(source_path), f"{symbol}()"))
    if offset is None:
        offset = offset_map.get((str(source_path), symbol))
    if offset is None:
        detail = escalations[0].detail if escalations else f"cannot locate {symbol}"
        raise LookupError(detail)

    changes = Rename(project, resource, offset=offset).get_changes(_symbol_name(new_leaf))
    project.do(changes)
    return AppliedAction(
        kind="rename",
        description=f"Renamed {old_name} -> {new_name}",
        history_index=len(project.history.undo_list),
    )


def _apply_rename_map(rename_map: RenameMap, repo_root: Path) -> ApplyResult:
    applied: list[AppliedAction] = []
    escalations: list[Escalation] = []

    project = Project(str(repo_root))
    try:
        for entry in rename_map.entries:
            try:
                applied.append(
                    _rename_entry(repo_root, project, entry.old_name, entry.new_name)
                )
            except Exception as exc:
                escalations.append(
                    Escalation(
                        kind="move_error",
                        symbol_id=entry.old_name,
                        detail=str(exc),
                    )
                )
    finally:
        project.close()

    return ApplyResult(applied=applied, escalations=escalations)


@app.command()
def analyze(repo: Path = typer.Argument(..., help="Path to repository")) -> None:
    """Build view, plan, and dry-run structure report."""
    graph_path = ensure_graph(repo)
    source_map = normalize_source_files(graph_path, repo)
    view = build_view(graph_path)
    refactor_plan = build_plan(view, repo, graph_path, source_map=source_map)

    plan_path = _plan_path(repo)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    write_plan(refactor_plan, plan_path)

    report_path = _refactor_dir(repo) / "STRUCTURE_REPORT.md"
    render_dry_run_report(refactor_plan, view, report_path)
    typer.echo(
        f"wrote refactor_plan.json and STRUCTURE_REPORT.md "
        f"({len(refactor_plan.file_moves)} file_moves, "
        f"{len(refactor_plan.symbol_moves)} symbol_moves)"
    )


@app.command("apply")
def apply_command(
    repo: Path = typer.Argument(..., help="Path to repository"),
    approve_symbols: bool = typer.Option(
        False,
        "--approve-symbols",
        help="Approve every proposed symbol move before applying",
    ),
    review_symbols: bool = typer.Option(
        False,
        "--review-symbols",
        help="Prompt for each proposed symbol move before applying",
    ),
) -> None:
    """Apply approved file and symbol moves, then validate."""
    if approve_symbols and review_symbols:
        typer.echo("choose only one of --approve-symbols or --review-symbols", err=True)
        raise typer.Exit(code=1)
    refactor_plan = _load_plan(repo)
    graph_path = ensure_graph(repo)
    source_map = normalize_source_files(graph_path, repo)
    if approve_symbols or review_symbols:
        approved = _approve_symbol_moves(
            refactor_plan,
            approve_all=approve_symbols,
            review=review_symbols,
        )
        _save_plan(refactor_plan, repo)
        typer.echo(f"approved {approved} symbol moves")
    result = apply_plan(refactor_plan, repo, only_approved_symbols=True, source_map=source_map)
    _validate_or_exit(repo, result)
    stale_graph = _graph_path(repo)
    if stale_graph.exists():
        stale_graph.unlink()
    typer.echo(f"applied {len(result.applied)} actions; validation passed")


@app.command("split")
def split_command(
    repo: Path = typer.Argument(..., help="Path to repository"),
    apply_: bool = typer.Option(False, "--apply", help="Apply approved split moves"),
    approve_splits: bool = typer.Option(
        False,
        "--approve-splits",
        help="Approve every proposed split move before applying",
    ),
    review_splits: bool = typer.Option(
        False,
        "--review-splits",
        help="Prompt for each proposed split move before applying",
    ),
) -> None:
    """Build or apply the splitter plan."""
    graph_path = ensure_graph(repo)
    source_map = normalize_source_files(graph_path, repo)
    if approve_splits and review_splits:
        typer.echo("choose only one of --approve-splits or --review-splits", err=True)
        raise typer.Exit(code=1)

    if apply_ and _split_plan_path(repo).exists():
        split_plan = _load_split_plan(repo)
    else:
        view = build_view(graph_path)
        graph = load_graph(graph_path)
        split_plan = build_split_plan(view, graph, repo, source_map=source_map)
        _write_split_plan(split_plan, repo)

    if not apply_:
        typer.echo(f"wrote split_plan.json ({len(split_plan.splits)} splits)")
        return

    if approve_splits or review_splits:
        approved = _approve_split_moves(
            split_plan,
            approve_all=approve_splits,
            review=review_splits,
        )
        _write_split_plan(split_plan, repo)
        typer.echo(f"approved {approved} split moves")
    result = apply_split_plan(split_plan, repo, only_approved=True, source_map=source_map)
    _validate_or_exit(repo, result)
    typer.echo(f"applied {len(result.applied)} split actions; validation passed")


@app.command("clean")
def clean_command(
    repo: Path = typer.Argument(..., help="Path to repository"),
    apply_: bool = typer.Option(False, "--apply", help="Delete approved dead code"),
    confirmed: bool = typer.Option(False, "--confirmed", help="Confirm deletion"),
    approve_deletions: bool = typer.Option(
        False,
        "--approve-deletions",
        help="Approve every proposed deletion before applying",
    ),
    review_deletions: bool = typer.Option(
        False,
        "--review-deletions",
        help="Prompt for each proposed deletion before applying",
    ),
) -> None:
    """Build or apply the dead-code report."""
    graph_path = ensure_graph(repo)
    source_map = normalize_source_files(graph_path, repo)
    if approve_deletions and review_deletions:
        typer.echo(
            "choose only one of --approve-deletions or --review-deletions",
            err=True,
        )
        raise typer.Exit(code=1)

    if apply_ and not confirmed:
        typer.echo(_CONFIRM_DELETE_MESSAGE, err=True)
        raise typer.Exit(code=1)

    report_path = _refactor_dir(repo) / "dead_code_report.json"
    if apply_ and report_path.exists():
        report = load_dead_code_report(repo)
    else:
        view = build_view(graph_path)
        graph = load_graph(graph_path)
        report = build_dead_code_report(view, graph, repo, source_map=source_map)
        save_dead_code_report(report, repo)
        md_path = _refactor_dir(repo) / "DEAD_CODE_REPORT.md"
        md_path.write_text(render_dead_code_report_md(report), encoding="utf-8")

    if not apply_:
        typer.echo(f"wrote dead_code_report.json ({len(report.symbols)} symbols)")
        return

    if approve_deletions or review_deletions:
        approved = _approve_dead_symbols(
            report,
            approve_all=approve_deletions,
            review=review_deletions,
        )
        save_dead_code_report(report, repo)
        typer.echo(f"approved {approved} deletions")
    result = apply_dead_code_report(report, repo, confirmed=True, source_map=source_map)
    _validate_or_exit(repo, result)
    typer.echo(f"applied {len(result.applied)} clean actions; validation passed")


@app.command("name")
def name_command(
    repo: Path = typer.Argument(..., help="Path to repository"),
    apply_: bool = typer.Option(False, "--apply", help="Apply rename_map.json"),
) -> None:
    """Write or apply the semantic rename map."""
    graph_path = ensure_graph(repo)
    source_map = normalize_source_files(graph_path, repo)  # noqa: F841 — available for future use
    refactor_plan = _load_plan(repo)

    rename_path = _rename_map_path(repo)
    if apply_ and rename_path.exists():
        rename_map = RenameMap.model_validate_json(rename_path.read_text(encoding="utf-8"))
    else:
        view = build_view(graph_path)
        rename_map = propose_rename_map(refactor_plan, view, repo, graph_path)
        rename_path.parent.mkdir(parents=True, exist_ok=True)
        write_rename_map(rename_map, rename_path)

    if not apply_:
        typer.echo(f"wrote rename_map.json ({len(rename_map.entries)} entries)")
        return

    result = _apply_rename_map(rename_map, repo)
    _validate_or_exit(repo, result)
    typer.echo(f"applied {len(result.applied)} rename actions; validation passed")


if __name__ == "__main__":
    app()
