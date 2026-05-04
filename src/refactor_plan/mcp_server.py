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

from mcp.server.fastmcp import FastMCP

from refactor_plan.applicator.models import AppliedAction, ApplyResult, Escalation
from refactor_plan.applicator.name_apply import apply_rename_map as do_apply_rename_map
from refactor_plan.applicator.rollback import rollback as do_rollback
from refactor_plan.applicator.rope_rename import rename_module as do_rename_module
from refactor_plan.applicator.rope_rename import rename_symbol as do_rename_symbol
from refactor_plan.applicator.worktree import (
    commit_and_release,
    create_worktree,
    discard_worktree,
    translate_plan,
)
from refactor_plan.interface.cluster_view import build_view
from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.naming.docstringer import build_docstring_context, insert_docstring_text
from refactor_plan.naming.namer import RenameEntry, RenameMap, build_naming_context
from refactor_plan.planning.planner import RefactorPlan
from refactor_plan.planning.planner import plan as build_plan
from refactor_plan.planning.planner import write_plan
from refactor_plan.reporting.reporter import render_dry_run_report, write_report
from refactor_plan.validation.validator import validate as do_validate

mcp = FastMCP(
    "codescaffold",
    instructions=(
        "Graph-driven structural refactoring tools. "
        "Typical workflow: analyze → get_cluster_context (you name the clusters) → "
        "apply_rename_map → merge_sandbox. "
        "For ad-hoc work: rename → merge_sandbox. "
        "apply_rename_map and rename default to sandbox=True: changes are validated "
        "in a git worktree and committed to a branch for review before merging."
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
        if cmd.exit_code != 0 and cmd.stderr:
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
    }
    report = render_dry_run_report(plan_dict, str(root))
    write_report(report, out / "STRUCTURE_REPORT.md")
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


# ---------------------------------------------------------------------------
# Cluster naming (context only — Claude Code supplies the names)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_cluster_context(repo: str = "") -> str:
    """Return structured context for each placeholder cluster.

    Shows each pkg_NNN package's files, classes, functions, and cross-cluster
    dependencies.  Use this to understand the clusters and choose semantic
    snake_case names.  Then call apply_rename_map with your choices.
    """
    root = _repo(repo)
    graph_path = ensure_graph(root)
    view = build_view(graph_path)
    plan = _load_plan(root)
    context = build_naming_context(plan, view)
    if not context:
        return "No clusters with placeholder names found in the current plan."
    return (
        context
        + "\n\n"
        "Suggest a snake_case name for each pkg_NNN above, then call "
        "apply_rename_map with a JSON object like: "
        '{"pkg_001": "auth", "pkg_002": "pipeline"}'
    )


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

        validation = do_validate(wt_path)
        if not validation.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (validation) — worktree discarded.\n" + _format_validation(validation)

        commit_and_release(root, wt_path, "refactor: apply rename map")
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

        validation = do_validate(wt_path)
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
    result = subprocess.run(
        ["git", "-C", str(root), "merge", "--no-ff", branch,
         "-m", f"refactor: merge {branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"FAILED: git merge returned:\n{result.stderr.strip()}"
    return f"Merged '{branch}' into current branch.\n{result.stdout.strip()}"


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
