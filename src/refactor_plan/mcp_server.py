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

from mcp.server.fastmcp import FastMCP
from refactor_plan.interface.apply_ops import merge_sandbox, rename, apply_rename_map, apply
from refactor_plan.interface.graph_ops import get_cluster_context
from refactor_plan.planning.approval import approve_moves
from refactor_plan.reporting.analysis import main, validate, analyze


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

from refactor_plan.server_helpers import _OUT_DIR  # noqa: F401


if __name__ == "__main__":
    main()
