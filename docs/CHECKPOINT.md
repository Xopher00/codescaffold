# Checkpoint — Graph Freshness + Clean Layer Contracts

## State

All 9 layers implemented and tested.
253 tests pass (excluding pre-existing failures in `tests/test_contracts.py`).
`python -m compileall src/codescaffold/` is clean.
`validate_contracts` passes: 1 contract kept, 0 broken.
`contracts` + `validate_contracts` via MCP produce clean layers with no violations.

## Test command

```bash
/home/scanbot/qwen/.venv/bin/python -m pytest tests/
```

System Python (3.11) lacks `rope_mcp_server`; use the qwen venv (3.12).

## What was built

| Layer | Module | Status |
|---|---|---|
| Rope wrappers | `codescaffold.operations` | done |
| Graphify wrappers | `codescaffold.graphify` | done |
| Candidates | `codescaffold.candidates` | done |
| Plan schema + store | `codescaffold.plans` | done |
| Sandbox | `codescaffold.sandbox` | done |
| Validation | `codescaffold.validation` | done |
| Audit | `codescaffold.audit` | done |
| MCP tools + server | `codescaffold.mcp` | done |
| Contracts | `codescaffold.contracts` | done |

## Do not redo

- **JSON boundary in `_unwrap`** (`operations/rope_ops.py`): parses rope's JSON string once, raises typed exceptions. No other module sees a raw JSON string.
- **No private graphify symbols**: `_is_file_node`, `_is_concept_node` are not imported anywhere in codescaffold. All analysis goes through the public API.
- **12-tool MCP surface**: `analyze`, `get_cluster_context`, `approve_moves`, `apply`, `validate`, `merge_sandbox`, `discard_sandbox`, `reset`, `contracts`, `validate_contracts`, `update_contract`, `propose_violation_fix`. Rope primitives and raw graphify functions are internal only.
- **sha256 staleness signal**: `plan.graph_hash` is compared to current snapshot hash in `assert_fresh()` before any approved move is executed.
- **Sandbox commit before validation**: `apply` commits the rope changes in the worktree before running `run_validation`, so compileall/pytest run against the committed state.
- **GraphSnapshot is directed**: `run_extract` defaults `directed=True`. `_hash_graph` uses directed edge pairs `(u, v)` for DiGraph (preserves direction in the hash). Undirected path still exists but is not the default.
- **Contracts are opt-in**: `.importlinter` absent → zero overhead in `apply` and `run_validation`. Contract validation only runs when the file exists.
- **Cycle gate on contract emit**: `generate_importlinter_config` refuses to write `.importlinter` when package cycles exist, returning cycle-break `MoveCandidate`s instead.
- **Graph freshness on every `run_extract` call**: `run_extract` now detects stale files (in the semantic cache but with an invalid AST hash) and re-extracts them. After assembling the combined extraction, it adds bare stub node entries for any edge endpoint that has no node entry, so `build_from_json` does not silently drop cross-package `imports_from` edges. Imports centralized in `graphify/vendor.py`: `cached_files`, `check_semantic_cache`, `save_semantic_cache`.
- **Package-reference node recognition in `build_package_dag`**: graphify emits `imports_from` edges whose targets are package-reference nodes (e.g. `codescaffold_sandbox`) — bare IDs with no `source_file`. `_pkg_ref_node_to_package` in `contracts/package_graph.py` recognises the `{root}_{subpkg}` pattern and maps these to dotted package names, so the DAG captures cross-package import direction correctly.
- **Layer violations root cause**: violations were not false cycles — the graphify semantic cache held the correct directed `imports_from` edges, but they were being dropped because their target nodes had no entries in the node list. Fix: stub nodes + `_pkg_ref_node_to_package`. Do not attempt to switch to grimp for layer ordering.

## Deferred

- Rope features not on golden path: `extract_method`, `inline_variable`, `convert_module_to_init`, `convert_module_to_package`, `move_and_rename_module`
- Rename-map flow (`apply_rename_map` MCP tool)
- Docstring insertion (LibCST-based)
- Rollback/manifest
- Contract caching (`no_cache=False` + `cache_dir` for large repos)
- **Pynguin test generation** — `generate_tests(source_file, repo_path)` MCP tool; subprocess isolation
  into repo venv required; output written into a sandbox worktree for review before commit
- future plans (drafted, unfinished): /home/scanbot/.claude/plans/tender-imagining-bird.md 