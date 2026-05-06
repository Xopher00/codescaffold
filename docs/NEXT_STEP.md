# Next Step — Rename-map flow (immediate)

## Problem

Add `apply_rename_map` MCP tool: batch-rename symbols or modules from a
user-supplied name mapping, applying all renames in a single sandbox pass.

## Bounded scope

```
src/codescaffold/operations/rename_ops.py   — thin wrapper over rope rename_symbol / rename_module
src/codescaffold/mcp/tools.py               — add apply_rename_map tool
src/codescaffold/mcp/server.py              — register the tool
tests/test_rename_map.py                    — unit + integration tests
```

## Constraints

- Input: `{old_name: new_name, ...}` mapping (symbol or module names).
- Each rename is a separate Rope operation; stop-on-first-error policy.
- Sandbox + commit + validate (same as `apply`).
- Returns audit summary identical in shape to `apply`.

## Deferred from this step

- Docstring insertion (LibCST-based)
- Rollback/manifest
- Contract caching
- **Pynguin test generation** — `generate_tests(source_file, repo_path)` MCP tool wrapping
  [pynguin](https://github.com/se2p/pynguin) for automatic test generation. Design notes:
  - Run pynguin via subprocess into the repo's own venv (same isolation pattern as `run_validation`);
    never import pynguin into the MCP server process — it instruments modules at runtime.
  - Write generated tests into a sandbox worktree so the agent can review and approve before committing,
    mirroring the `apply → audit → merge_sandbox` flow.
