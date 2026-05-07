# Checkpoint — Graph-to-Rope Preflight Resolution

## State

`python -m compileall src/codescaffold/` is clean.
`lint-imports` passes: 1 contract kept, 0 broken.
`contracts` MCP tool regenerated `.importlinter` with `bridge` at Layer 1 (peer to `candidates`).
130 tests pass; 5 pre-existing failures in `test_contracts.py` (unrelated to this work).

## What was done

- `src/codescaffold/bridge/` — new package:
  - `resolution.py` — `RopeResolution` frozen dataclass, `ResolutionStatus` and `PreflightStatus` type aliases.
  - `preflight.py` — `resolve_candidate`, `resolve_candidates` (caches `list_symbols` per file, calls `close_rope_project` once), `preflight_status`. Uses a `_Candidate` Protocol to avoid importing `candidates` (same layer peer).
- `src/codescaffold/plans/schema.py` — `RopeResolutionRecord` pydantic model; `CandidateRecord` extended with `resolution` and `preflight` optional fields (backwards-compatible with old plan JSON).
- `src/codescaffold/plans/store.py` — `candidates_to_records` extended with optional `resolutions` parameter; `_preflight_from_status` inlined to avoid importing bridge (which would cross layers).
- `src/codescaffold/plans/__init__.py` — exports `RopeResolutionRecord`.
- `src/codescaffold/mcp/tools.py` — `analyze` calls `resolve_candidates` and stamps candidates; markdown shows `N ready / M needs_review / K blocked` summary and per-row tags. `approve_moves` blocks `blocked` candidates, warns on `needs_review` and handcrafted moves.
- `tests/fixtures/preflight_repo/sample.py` — minimal fixture for rope integration tests.
- `tests/test_preflight.py` — 20 tests covering all resolution statuses, batching, `close_rope_project` call count, real rope integration, and `approve_moves` gate.

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
- The 5 failures in `test_contracts.py` are pre-existing and not caused by this work.
