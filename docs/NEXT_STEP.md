# Next Step — Pynguin test generation (immediate)

## Problem

Add a `generate_tests(source_file, repo_path)` MCP tool that wraps
[pynguin](https://github.com/se2p/pynguin) for automatic test generation.

## Bounded scope

```
src/codescaffold/operations/pynguin_ops.py   — subprocess runner; never import pynguin directly
src/codescaffold/mcp/tools.py               — add generate_tests tool
src/codescaffold/mcp/server.py              — register the tool
tests/test_pynguin_ops.py                   — unit + integration tests
```

## Constraints

- Run pynguin via subprocess into the repo's own venv — same isolation pattern as `run_validation`.
- Never import pynguin into the MCP server process — it instruments modules at runtime.
- Write generated tests into a sandbox worktree so the agent can review and approve before
  committing, mirroring the `apply → audit → merge_sandbox` flow.
- No new layer edges. `lint-imports` must stay green.

## Deferred from earlier steps

- Docstring insertion (LibCST-based)
- Rollback/manifest
- Contract caching
