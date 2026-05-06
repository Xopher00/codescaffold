"""Codescaffold MCP server — curated graph-driven refactoring workflow."""

from mcp.server.fastmcp import FastMCP

from codescaffold.mcp.tools import (
    analyze,
    approve_moves,
    apply,
    contracts,
    discard_sandbox,
    get_cluster_context,
    merge_sandbox,
    propose_violation_fix,
    reset,
    update_contract,
    validate,
    validate_contracts,
)

mcp = FastMCP(
    "codescaffold",
    instructions=(
        "Graph-driven, plan-mediated Python refactoring. "
        "Workflow: analyze → get_cluster_context → approve_moves → apply → validate → merge_sandbox. "
        "All mutations happen inside a git worktree sandbox. "
        "Merge only after reviewing the apply audit."
    ),
)

for _fn in [
    analyze,
    get_cluster_context,
    approve_moves,
    apply,
    validate,
    merge_sandbox,
    discard_sandbox,
    reset,
    contracts,
    validate_contracts,
    update_contract,
    propose_violation_fix,
]:
    mcp.tool()(_fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
