# Checkpoint — Rename-Map Flow (`apply_rename_map`)

## State

`python -m compileall src/codescaffold/` is clean.
`lint-imports` passes: 1 contract kept, 0 broken.
153 tests pass, 0 failures. All test_contracts.py failures resolved.

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

## Do-not-redo

- The `_Candidate` Protocol in `bridge/preflight.py` is intentional — avoids a peer-layer import violation (`bridge` and `candidates` are both Layer 1).
- `_preflight_from_status` in `plans/store.py` is intentional — avoids importing bridge into plans.
- `rename_symbol_batch` uses a single rope project session with one `close_rope_project` in `finally` — do NOT add per-rename close calls; that discards rope's working state mid-batch and breaks multi-rename-in-one-file flows.
- `ApprovedRename` is NOT overloaded onto `ApprovedMove` — they are semantically separate; renames live in `Plan.approved_renames`.
- `apply_rename_map` reuses `bridge.resolve_candidates` via a local `_RenameAdapter` (satisfies `_Candidate` Protocol) — do not add a parallel resolver.
- The 5 failures in `test_contracts.py` are pre-existing and not caused by this work.
