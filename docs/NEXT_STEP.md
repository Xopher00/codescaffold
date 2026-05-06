# Next Step — Fix import-linter layer violations (immediate)

## Problem

`validate_contracts` reports 3 violations after `contracts` generates `.importlinter`:

1. `mcp` and `sandbox` land in the same layer (mcp imports sandbox).
2. `contracts` is in a higher layer than `plans` (contracts imports plans).
3. `candidates` and `graphify` land in the same layer.

## Root cause

`build_package_dag` in `contracts/package_graph.py` needs the graphify snapshot
loaded as a **directed graph**. The AST-extracted `calls`/`uses`/`imports_from`
edges are all directional. When the graph is loaded undirected, edge direction
is lost and the DAG gets spurious cross-edges that create false cycles (→ SCC
condensation collapses packages into the same layer).

The fix applied in the previous session was to pass `directed=True` to
`build_from_json` inside `run_extract`. Confirm that path is still correct,
then verify contracts generate clean layers.

## Files

- `src/codescaffold/graphify/extract.py` — `run_extract(directed=True)` ← confirm
- `src/codescaffold/contracts/package_graph.py` — `build_package_dag()` edge filter

## Verify

Run `contracts` then `validate_contracts` via MCP. All 3 violations should
be gone and the layers should reflect actual import depth.

---

# Queued — Rename-map flow

## Task

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
