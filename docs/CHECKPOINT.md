# Checkpoint — Grimp DAG Swap + Module Rename Extension

## State

`python -m compileall src/codescaffold/` is clean.
`lint-imports` passes: 1 contract kept, 0 broken.
167 tests pass, 0 failures (excluding `tests/old/` — legacy pre-rebuild suite).

## What was done

### Preflight resolution (prior step)

- `src/codescaffold/bridge/` — new package:
  - `resolution.py` — `RopeResolution` frozen dataclass, `ResolutionStatus` and `PreflightStatus` type aliases.
  - `preflight.py` — `resolve_candidate`, `resolve_candidates` (caches `list_symbols` per file, calls `close_rope_project` once), `preflight_status`. Uses a `_Candidate` Protocol to avoid importing `candidates` (same layer peer).
- `src/codescaffold/plans/schema.py` — `RopeResolutionRecord` pydantic model; `CandidateRecord` extended with `resolution` and `preflight` optional fields (backwards-compatible with old plan JSON).
- `src/codescaffold/plans/store.py` — `candidates_to_records` extended with optional `resolutions` parameter; `_preflight_from_status` inlined to avoid importing bridge (which would cross layers).
- `src/codescaffold/plans/__init__.py` — exports `RopeResolutionRecord`.
- `src/codescaffold/mcp/tools.py` — `analyze` calls `resolve_candidates` and stamps candidates; markdown shows `N ready / M needs_review / K blocked` summary and per-row tags. `approve_moves` blocks `blocked` candidates, warns on `needs_review` and handcrafted moves.
- `tests/fixtures/preflight_repo/sample.py` — minimal fixture for rope integration tests.
- `tests/test_preflight.py` — 20 tests covering all resolution statuses, batching, `close_rope_project` call count, real rope integration, and `approve_moves` gate.

### Rename-map flow (this step)

- `src/codescaffold/plans/schema.py` — `ApprovedRename` pydantic model (sibling of `ApprovedMove`); `Plan.approved_renames: list[ApprovedRename]` (backwards-compat default `[]`).
- `src/codescaffold/plans/__init__.py` — exports `ApprovedRename`.
- `src/codescaffold/operations/rename_ops.py` — new: `RenameEntry`, `BatchRenameResult`, `rename_symbol_batch`. Single rope session per batch; one `close_rope_project` in `finally`; stop-on-first-error.
- `src/codescaffold/operations/__init__.py` — exports `RenameEntry`, `BatchRenameResult`, `rename_symbol_batch`.
- `src/codescaffold/audit/record.py` — `ApplyAudit.renames_applied: tuple[ApprovedRename, ...] = ()`; `to_json()` updated.
- `src/codescaffold/mcp/tools.py` — `apply_rename_map` tool: preflight via `bridge.resolve_candidates` (local `_RenameAdapter`), blocks on any `blocked`, warns on `needs_review`, persists Plan, single-session rename batch in sandbox, commit + validate + `ApplyAudit`.
- `src/codescaffold/mcp/server.py` — `apply_rename_map` registered.
- `tests/fixtures/rename_repo/sample.py`, `caller.py` — rename fixtures.
- `tests/test_rename_map.py` — 18 tests: unit (mocked rope), real rope integration, MCP gate, schema compat.

## Layer layout (current)

```
graphify | operations    (Layer 0)
bridge | candidates      (Layer 1)
plans                    (Layer 2)
contracts                (Layer 3)
validation               (Layer 4)
audit | sandbox          (Layer 5)
mcp                      (Layer 6)
```

## Test command

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/old
```

### Grimp DAG swap + module rename extension (this step)

**Track B — Grimp swap:**
- `src/codescaffold/contracts/package_graph.py` — `build_package_dag` rebuilt on grimp; `_pkg_ref_node_to_package` deleted (dead — its only caller was the old builder); `_file_to_subpackage` kept (used by 3 callers for graphify path→pkg mapping); `_prepend_syspath` context manager with `importlib.invalidate_caches()` on both insert and remove; post-call `sys.modules` purge scoped to *newly added* entries only (avoids wiping the running package on drift-guard tests).
- `src/codescaffold/contracts/cycles.py` — `detect_package_cycles(repo_path, snap)` signature; `Path` import added.
- `src/codescaffold/contracts/generator.py` — threads `repo_path` into `detect_package_cycles` and `build_package_dag`.
- `src/codescaffold/contracts/violation_fix.py` — dropped unused `build_package_dag` import.
- `tests/fixtures/grimp_repos/{simple,subpackages,with_external,cyclic}/` — new fixture repos.
- `tests/test_grimp_dag.py` — 7 tests: edge direction, subpackage squash, external exclusion, cycle detection, sys.path restoration, drift guard.
- `tests/test_contracts.py` — `TestBuildPackageDag`, `TestDetectPackageCycles`, `test_cyclic_does_not_write` rewritten for new grimp-based signatures + `_make_import_pkg` helper.

**Track A — Module rename extension:**
- `src/codescaffold/plans/schema.py` — `ApprovedMove.new_name: str | None = None` (module-only; backwards-compatible).
- `src/codescaffold/operations/rope_ops.py` — `move_and_rename_module` wrapper; strips `.py` suffix from `new_name` before passing to rope (rope expects bare module name).
- `src/codescaffold/operations/__init__.py` — exports `move_and_rename_module`.
- `src/codescaffold/mcp/tools.py` — module dispatch branch extended: `if move.new_name → move_and_rename_module` else `move_module`.
- `tests/test_module_ops.py` — 7 tests: rope integration (move+rename, import update), schema compat.

## Do-not-redo

- The `_Candidate` Protocol in `bridge/preflight.py` is intentional — avoids a peer-layer import violation (`bridge` and `candidates` are both Layer 1).
- `_preflight_from_status` in `plans/store.py` is intentional — avoids importing bridge into plans.
- `rename_symbol_batch` uses a single rope project session with one `close_rope_project` in `finally` — do NOT add per-rename close calls; that discards rope's working state mid-batch and breaks multi-rename-in-one-file flows.
- `ApprovedRename` is NOT overloaded onto `ApprovedMove` — they are semantically separate; renames live in `Plan.approved_renames`.
- `apply_rename_map` reuses `bridge.resolve_candidates` via a local `_RenameAdapter` (satisfies `_Candidate` Protocol) — do not add a parallel resolver.
- `_pkg_ref_node_to_package` is deleted and must not be re-added — it was a workaround for graphify synthetic nodes; grimp eliminates the need.
- `_file_to_subpackage` is retained — three callers (cycles, generator, violation_fix) map graphify `source_file` strings to package names; grimp speaks module names not paths.
- `_propose_cycle_break` stays graphify-driven — grimp does not see symbols; only graphify can score which symbol breaks a cycle.
- The `sys.modules` purge in `build_package_dag` is scoped to newly-added entries only — do NOT purge pre-existing modules (e.g., `codescaffold.*`) or mock patches on those modules break in the test suite.
- `move_and_rename_module` strips `.py` from `new_name` — rope's `move_and_rename_module` expects a bare module name; passing `"renamed.py"` produces `"renamed.py.py"`.
