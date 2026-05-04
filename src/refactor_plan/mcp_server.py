"""MCP server for codescaffold — graph-driven refactoring tools for Claude Code.

Each tool either returns structured context for Claude Code to reason over, or
performs a purely mechanical operation (rope rename, import rewrite, docstring
insert).  Neither tool calls the Anthropic API; Claude Code is the LLM.

Configuration
-------------
Set CODESCAFFOLD_REPO to the repository root so you don't have to pass it on
every call.  Individual tool calls can override it with an explicit ``repo``
argument.

    export CODESCAFFOLD_REPO=/path/to/my-project

Sandbox
-------
apply_rename_map and rename default to sandbox=True, which runs changes in a
git worktree on a fresh branch, validates, then commits and removes the
worktree directory (keeping the branch).  Merge when ready:

    git merge <branch>

Pass sandbox=False to apply directly (faster, no safety net).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import networkx as nx
from mcp.server.fastmcp import FastMCP
from refactor_plan.layout import detect_layout
from refactor_plan.contracts.import_contracts import (
    check_staleness,
    generate_contracts as do_generate_contracts,
)
from refactor_plan.interface.cluster_view import ClusterView, build_view
from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.naming.docstringer import build_docstring_context, insert_docstring_text
from refactor_plan.naming.namer import RenameEntry, RenameMap, build_naming_context
from refactor_plan.planning.planner import FileMoveProposal, RefactorPlan
from refactor_plan.planning.planner import plan as build_plan
from refactor_plan.planning.planner import write_plan
from refactor_plan.reporting.reporter import render_dry_run_report, write_report
from refactor_plan.validation.validator import validate as do_validate
from refactor_plan.naming.name_apply import apply_rename_map as do_apply_rename_map
from refactor_plan.interface.worktree import commit_and_release, create_worktree, create_worktree_from_branch, discard_worktree, load_state, save_state, translate_plan
from refactor_plan.execution.apply import _cleanup_empty_source_dirs, _ensure_package_inits, _run_file_moves, _run_import_rewrites, apply_plan as do_apply_plan
from refactor_plan.execution.rope_rename import rename_module as do_rename_module, rename_symbol as do_rename_symbol
from refactor_plan.records.rollback import rollback as do_rollback
from refactor_plan.execution.models import AppliedAction, ApplyResult, Escalation
from refactor_plan.records.manifests import write_manifest

mcp = FastMCP(
    "codescaffold",
    instructions=(
        "Graph-driven structural refactoring tools. "
        "Workflow: analyze → get_cluster_context (review graph evidence, decide placement "
        "and names) → approve_moves → apply → apply_rename_map → merge_sandbox. "
        "apply and apply_rename_map are chained: apply commits structural moves to a "
        "branch; apply_rename_map builds on top of that branch and produces the final "
        "merge-ready branch. Never merge the apply branch directly. "
        "For ad-hoc renames: rename → merge_sandbox."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OUT_DIR = ".refactor_plan"


def _repo(repo: str) -> Path:
    """Resolve repo path from argument or CODESCAFFOLD_REPO env var."""
    path = repo or os.environ.get("CODESCAFFOLD_REPO", "")
    if not path:
        raise ValueError(
            "repo argument is required (or set CODESCAFFOLD_REPO env var)"
        )
    return Path(path).resolve()


def _out_dir(root: Path) -> Path:
    return root / _OUT_DIR


def _plan_path(root: Path) -> Path:
    return _out_dir(root) / "refactor_plan.json"


def _load_plan(root: Path) -> RefactorPlan:
    path = _plan_path(root)
    if not path.exists():
        raise FileNotFoundError(f"No plan at {path} — run analyze first")
    return RefactorPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _summarise_result(result: ApplyResult) -> str:
    lines = [
        f"Applied: {len(result.applied)}  "
        f"Failed: {len(result.failed)}  "
        f"Skipped: {len(result.skipped)}"
    ]
    for e in result.failed + result.skipped:
        lines.append(f"  [{e.category}] {e.source}: {e.reason}")
    return "\n".join(lines)


def _format_validation(report: object) -> str:
    lines = []
    for cmd in report.commands:  # type: ignore[union-attr]
        mark = "OK" if cmd.exit_code == 0 else "FAIL"
        lines.append(f"  [{mark}] {cmd.command}")
        if cmd.exit_code != 0:
            if cmd.stdout:
                lines.append(cmd.stdout[-600:])
            if cmd.stderr:
                lines.append(cmd.stderr[:400])
    return "\n".join(lines)


def _sandbox_result(branch: str, summary: str) -> str:
    return (
        f"{summary}\n\n"
        f"Validation PASSED. Changes committed to branch '{branch}'.\n"
        f"Review : git diff HEAD...{branch}\n"
        f"Apply  : git merge {branch}\n"
        f"Discard: git branch -D {branch}"
    )


# ---------------------------------------------------------------------------
# Structural analysis
# ---------------------------------------------------------------------------

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
def approve_moves(moves_json: str, repo: str = "") -> str:
    """Record model-approved file moves into the plan for the next apply.

    moves_json — JSON array of move objects:
      [{"source": "src/pkg/foo.py", "dest": "src/contracts/foo.py"}, ...]

    Files not listed stay where they are.
    Pass [] to clear previously approved moves.
    Validates all sources exist and destinations are within source_root.
    Writes approved file_moves to refactor_plan.json.
    Call apply next to execute.
    """
    root = _repo(repo)
    try:
        raw_moves: list[dict] = json.loads(moves_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    plan = _load_plan(root)
    layout = detect_layout(root)

    if not raw_moves:
        plan.file_moves = []
        write_plan(plan, _plan_path(root))
        return "Approved moves cleared."

    proposals: list[FileMoveProposal] = []
    errors: list[str] = []

    for entry in raw_moves:
        src = entry.get("source", "")
        dest = entry.get("dest", "")
        if not src or not dest:
            errors.append(f"  Missing source or dest in: {entry}")
            continue
        src_path = (root / src).resolve() if not Path(src).is_absolute() else Path(src).resolve()
        dest_path = (root / dest).resolve() if not Path(dest).is_absolute() else Path(dest).resolve()
        if not src_path.exists():
            errors.append(f"  Source not found: {src}")
            continue
        try:
            dest_path.relative_to(layout.source_root.resolve())
        except ValueError:
            errors.append(f"  Destination outside source_root ({layout.source_root}): {dest}")
            continue
        proposals.append(FileMoveProposal(
            source=str(src_path),
            dest=str(dest_path),
            dest_package=str(dest_path.parent),
        ))

    if errors:
        return "Validation errors — no moves written:\n" + "\n".join(errors)

    plan.file_moves = proposals
    write_plan(plan, _plan_path(root))

    placement_needed = sum(1 for d in plan.pending_decisions if d.needs_placement)
    return (
        f"{len(proposals)} move(s) approved and written to plan.\n"
        f"Pending placement decisions: {placement_needed}\n"
        "Call apply next to execute in a sandbox."
    )


@mcp.tool()
def approve_symbol_moves(moves_json: str, repo: str = "") -> str:
    """Mark symbol moves as approved for the next apply.

    moves_json — JSON array of move objects:
      [{"source": "src/pkg/foo.py", "dest": "src/other/bar.py", "symbol": "MyClass"}, ...]

    Accepts any valid source/dest/symbol triple — not limited to planner proposals.
    Validates that the source file exists and contains the named symbol.
    Pass [] to clear all approved symbol moves.
    Call apply next to execute approved moves in a sandbox.
    """
    root = _repo(repo)
    try:
        raw_moves: list[dict] = json.loads(moves_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    plan = _load_plan(root)

    if not raw_moves:
        for m in plan.symbol_moves:
            m.approved = False
        write_plan(plan, _plan_path(root))
        return "Symbol move approvals cleared."

    from refactor_plan.planning.planner import SymbolMoveProposal
    import ast

    proposals: list[SymbolMoveProposal] = []
    errors: list[str] = []

    for entry in raw_moves:
        src = entry.get("source", "")
        dest = entry.get("dest", "")
        sym = entry.get("symbol", "")
        if not src or not dest or not sym:
            errors.append(f"  Missing source, dest, or symbol in: {entry}")
            continue
        src_path = (root / src).resolve() if not Path(src).is_absolute() else Path(src).resolve()
        dest_path = (root / dest).resolve() if not Path(dest).is_absolute() else Path(dest).resolve()
        if not src_path.exists():
            errors.append(f"  Source not found: {src}")
            continue
        # Verify symbol exists in source file
        try:
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
            names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            if sym not in names:
                errors.append(f"  Symbol '{sym}' not found in {src_path}")
                continue
        except SyntaxError as exc:
            errors.append(f"  Cannot parse {src_path}: {exc}")
            continue
        proposals.append(SymbolMoveProposal(source=str(src_path), dest=str(dest_path), symbol=sym, approved=True))

    if errors:
        return "Validation errors — no moves written:\n" + "\n".join(errors)

    # Merge with existing unapproved planner proposals (keep them for reference)
    approved_keys = {(m.source, m.dest, m.symbol) for m in proposals}
    retained = [m for m in plan.symbol_moves if (m.source, m.dest, m.symbol) not in approved_keys]
    plan.symbol_moves = retained + proposals
    write_plan(plan, _plan_path(root))

    return (
        f"{len(proposals)} symbol move(s) approved.\n"
        "Call apply next to execute in a sandbox."
    )


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def apply(repo: str = "", sandbox: bool = True) -> str:
    """Apply the refactor plan (file moves + symbol moves + import rewrites).

    With sandbox=True (default), runs in a git worktree, validates, then commits
    to a branch for review.  Merge with:
        git merge <branch>
    Pass sandbox=False to apply directly to the working tree.
    """
    import rope.base.project as rp

    root = _repo(repo)
    plan = _load_plan(root)

    def _plan_dict(p: RefactorPlan) -> dict:
        return {
            "file_moves": [
                {"source": m.source, "dest": m.dest, "dest_package": m.dest_package}
                for m in p.file_moves
            ],
            "symbol_moves": [
                {"source": m.source, "dest": m.dest, "symbol": m.symbol}
                for m in p.symbol_moves
                if m.approved
            ],
            "source_root": p.source_root,
        }

    if not plan.file_moves and plan.pending_decisions:
        return (
            "No moves approved yet.\n"
            "Run get_cluster_context to review graph evidence, "
            "then approve_moves with your placement decisions."
        )

    if not sandbox:
        result = do_apply_plan(_plan_dict(plan), root, _out_dir(root))
        return _summarise_result(result)

    wt_path, branch = create_worktree(root)
    try:
        wt_plan = translate_plan(plan, root, wt_path)
        wt_out = _out_dir(wt_path)
        wt_out.mkdir(parents=True, exist_ok=True)
        write_plan(wt_plan, wt_out / "refactor_plan.json")

        layout = detect_layout(wt_path)
        wt_src = str(layout.source_root)
        wt_env = {"PYTHONPATH": wt_src}

        # Pre-create all destination package dirs before rope runs.
        # Rope creates the directory on the first move; subsequent moves to the
        # same directory fail with EEXIST.  Creating them up-front is idempotent.
        pre_dest_dirs = {Path(m.dest_package) for m in wt_plan.file_moves}
        _ensure_package_inits(pre_dest_dirs, layout.source_root)

        # --- Phase 1: file moves ---
        project = rp.Project(str(wt_path))
        file_moves = [
            {"source": m.source, "dest": m.dest, "dest_package": m.dest_package}
            for m in wt_plan.file_moves
        ]
        try:
            applied, failed, dest_dirs = _run_file_moves(project, file_moves, dry_run=False)
        finally:
            project.close()

        result = ApplyResult(applied=applied, failed=failed)
        if result.failed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (file moves) — worktree discarded.\n" + _summarise_result(result)

        # --- Phase 2: __init__.py creation + empty source dir cleanup ---
        _ensure_package_inits(dest_dirs, layout.source_root)
        _cleanup_empty_source_dirs(result.applied, layout.source_root)

        # --- Structural validation: compileall after moves ---
        v1 = do_validate(wt_path, env=wt_env, mode="structural", layout=layout)
        if not v1.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (structural — after moves) — worktree discarded.\n" + _format_validation(v1)

        # --- Installability check: can we import the package after moves? ---
        v2 = do_validate(wt_path, env=wt_env, mode="installability", layout=layout)
        if not v2.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (installability — after moves) — worktree discarded.\n" + _format_validation(v2)

        # --- Phase 3: import rewrites ---
        src_root = layout.source_root if wt_plan.source_root else None
        _, skipped = _run_import_rewrites(result.applied, wt_path, src_root)
        result.skipped.extend(skipped)

        # --- Structural validation: compileall after import rewrites ---
        v3 = do_validate(wt_path, env=wt_env, mode="structural", layout=layout)
        if not v3.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (structural — after import rewrites) — worktree discarded.\n" + _format_validation(v3)

        # --- Behavioral validation: pytest ---
        v4 = do_validate(wt_path, env=wt_env, mode="behavioral", layout=layout)
        if not v4.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (behavioral/pytest) — worktree discarded.\n" + _format_validation(v4)

        wt_out.mkdir(parents=True, exist_ok=True)
        write_manifest(result, wt_out)

        commit_and_release(root, wt_path, "refactor: apply file moves")

        # Only gate on rename if moves used pkg_NNN placeholder directories.
        # When destinations are already semantic names, the branch is final.
        import re as _re
        _placeholder_pattern = _re.compile(r"(^|[\\/])pkg_\d+($|[\\/])")
        _needs_rename = any(
            _placeholder_pattern.search(m.dest_package)
            for m in wt_plan.file_moves
        )
        if _needs_rename:
            save_state(_out_dir(root), pending_rename_branch=branch)

        # Refresh contracts to reflect the new module layout
        try:
            _gp = ensure_graph(root)
            _view = build_view(_gp)
            do_generate_contracts(plan, _view, _gp, root, layout, force=False)
        except Exception:
            pass

        if _needs_rename:
            return (
                f"{_summarise_result(result)}\n\n"
                f"Structural moves committed to branch '{branch}'.\n"
                f"DO NOT MERGE YET — placeholder names are not final.\n"
                f"Next: get_cluster_context → apply_rename_map\n"
                f"Discard: git branch -D {branch}"
            )
        return (
            f"{_summarise_result(result)}\n\n"
            f"Validation PASSED. Changes committed to branch '{branch}'.\n"
            f"Review : git diff HEAD...{branch}\n"
            f"Apply  : git merge {branch}\n"
            f"Discard: git branch -D {branch}"
        )

    except Exception:
        discard_worktree(root, wt_path, branch)
        raise


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


@mcp.tool()
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


@mcp.tool()
def apply_rename_map(rename_map_json: str, repo: str = "", sandbox: bool = True) -> str:
    """Rename placeholder packages to semantic names.

    rename_map_json — JSON object mapping placeholder names to new names,
    e.g. '{"pkg_001": "auth", "pkg_002": "pipeline"}'.

    Uses rope to rename each package directory and rewrite all imports.
    With sandbox=True (default), changes are applied in a git worktree,
    validated, then committed to a branch for review.  Merge with:
        git merge <branch>
    Pass sandbox=False to apply directly to the working tree.
    """
    root = _repo(repo)
    try:
        mapping: dict[str, str] = json.loads(rename_map_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    rename_map = RenameMap(entries=[
        RenameEntry(old_name=k, new_name=v) for k, v in mapping.items()
    ])

    if not sandbox:
        plan = _load_plan(root)
        result = do_apply_rename_map(rename_map, plan, root, _out_dir(root), dry_run=False)
        return _summarise_result(result)

    # --- sandboxed path ---
    plan = _load_plan(root)
    state = load_state(_out_dir(root))
    base_branch = state.get("pending_rename_branch")

    if base_branch:
        wt_path, branch = create_worktree_from_branch(root, base_branch)
    else:
        wt_path, branch = create_worktree(root)

    try:
        wt_plan = translate_plan(plan, root, wt_path)
        wt_out = _out_dir(wt_path)
        wt_out.mkdir(parents=True, exist_ok=True)
        write_plan(wt_plan, wt_out / "refactor_plan.json")

        result = do_apply_rename_map(rename_map, wt_plan, wt_path, wt_out, dry_run=False)

        if result.failed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (apply errors) — worktree discarded.\n" + _summarise_result(result)

        wt_layout = detect_layout(wt_path)
        wt_env = {"PYTHONPATH": str(wt_layout.source_root)}
        validation = do_validate(wt_path, env=wt_env, layout=wt_layout)
        if not validation.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (validation) — worktree discarded.\n" + _format_validation(validation)

        commit_and_release(root, wt_path, "refactor: apply rename map")

        # Clear the pending rename state now that renames are committed.
        if base_branch:
            save_state(_out_dir(root), pending_rename_branch=None)

        # Refresh contracts after renames change module names
        try:
            graph_path = ensure_graph(root)
            view = build_view(graph_path)
            _plan = _load_plan(root)
            _layout = detect_layout(root)
            do_generate_contracts(_plan, view, graph_path, root, _layout, force=False)
        except Exception:
            pass

        return _sandbox_result(branch, _summarise_result(result))

    except Exception:
        discard_worktree(root, wt_path, branch)
        raise


# ---------------------------------------------------------------------------
# Ad-hoc rename
# ---------------------------------------------------------------------------

@mcp.tool()
def rename(target: str, new_name: str, repo: str = "", sandbox: bool = True) -> str:
    """Rename a symbol, module, or package — propagates to all call sites.

    target formats:
      'src/pkg/mod.py::MyFunc'   — rename a function or class
      'src/pkg/mod.py'           — rename a module file
      'src/pkg/'                 — rename a package directory

    new_name is the simple identifier (no path, no extension).
    With sandbox=True (default), runs in a git worktree, validates, then
    commits to a branch.  Pass sandbox=False to apply directly.
    """
    root = _repo(repo)

    def _run_rename(base: Path) -> AppliedAction | Escalation:
        if "::" in target:
            file_part, symbol_name = target.split("::", 1)
            file_path = (base / file_part).resolve()
            return do_rename_symbol(base, file_path, symbol_name, new_name)
        module_path = (base / target).resolve()
        return do_rename_module(base, module_path, new_name)

    if not sandbox:
        action = _run_rename(root)
        if isinstance(action, Escalation):
            return f"FAILED: {action.reason}"
        return (
            f"Renamed '{target}' → '{new_name}'\n"
            f"  Strategy: {action.strategy.value if action.strategy else '?'}\n"
            f"  Files touched: {len(action.files_touched)}\n"
            f"  Imports rewritten: {action.imports_rewritten}"
        )

    # --- sandboxed path ---
    wt_path, branch = create_worktree(root)
    try:
        action = _run_rename(wt_path)

        if isinstance(action, Escalation):
            discard_worktree(root, wt_path, branch)
            return f"FAILED: {action.reason} — worktree discarded."

        wt_layout = detect_layout(wt_path)
        wt_env = {"PYTHONPATH": str(wt_layout.source_root)}
        validation = do_validate(wt_path, env=wt_env, layout=wt_layout)
        if not validation.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (validation) — worktree discarded.\n" + _format_validation(validation)

        commit_and_release(root, wt_path, f"refactor: rename '{target}' → '{new_name}'")
        summary = (
            f"Renamed '{target}' → '{new_name}'\n"
            f"  Strategy: {action.strategy.value if action.strategy else '?'}\n"
            f"  Files touched: {len(action.files_touched)}\n"
            f"  Imports rewritten: {action.imports_rewritten}"
        )
        return _sandbox_result(branch, summary)

    except Exception:
        discard_worktree(root, wt_path, branch)
        raise


# ---------------------------------------------------------------------------
# Sandbox management
# ---------------------------------------------------------------------------

@mcp.tool()
def merge_sandbox(branch: str, repo: str = "") -> str:
    """Merge a sandbox branch produced by apply_rename_map or rename.

    Runs git merge --no-ff so the refactor is recorded as a single merge commit.
    Safe to call only after reviewing the diff:
        git diff HEAD...<branch>
    """
    root = _repo(repo)

    state = load_state(_out_dir(root))
    if state.get("pending_rename_branch") == branch:
        return (
            f"WARNING: branch '{branch}' contains structural moves only — "
            f"placeholder names have not been replaced.\n"
            f"Run apply_rename_map first, then merge the resulting rename branch."
        )

    result = subprocess.run(
        ["git", "-C", str(root), "merge", "--no-ff", branch,
         "-m", f"refactor: merge {branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"FAILED: git merge returned:\n{result.stderr.strip()}"

    _reset_stale_artifacts(_out_dir(root))
    return f"Merged '{branch}' into current branch.\n{result.stdout.strip()}"


def _reset_stale_artifacts(out_dir: Path) -> None:
    """Remove plan, state, and import-linter contracts after a successful merge."""
    for name in ("refactor_plan.json", "state.json"):
        p = out_dir / name
        if p.exists():
            p.unlink()
    importlinter = out_dir.parent / ".importlinter"
    if importlinter.exists():
        importlinter.unlink()


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


# ---------------------------------------------------------------------------
# Docstring generation (context only — Claude Code writes the text)
# ---------------------------------------------------------------------------

@mcp.tool()
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


if __name__ == "__main__":
    main()
