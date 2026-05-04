# Project Status — codescaffold bootstrap attempt

## What we have

The MCP server is registered and functional (`codescaffold-mcp`). All Anthropic API calls have been removed. The server has these tools: `analyze`, `validate`, `rollback`, `apply`, `get_cluster_context`, `apply_rename_map`, `rename`, `merge_sandbox`, `discard_sandbox`, `get_symbol_context`, `insert_docstring`.

The sandbox mechanism uses git worktrees (`/tmp/codescaffold_<ts>`), commits on success, discards on failure. `sandbox=True` is the default for destructive tools.

## Where the apply pipeline is currently broken

We have been trying to bootstrap codescaffold by running it on itself. Every `apply` run reaches the validation step, passes `compileall`, then fails `pytest` with:

```
ModuleNotFoundError: No module named 'pkg_000'
```

Root cause sequence worked through so far:
1. **Relative paths in graph nodes** — fixed: planner resolves relative paths against `repo_root`
2. **`__init__.py` being proposed for moves** — fixed: planner skips `__init__.py`
3. **Duplicate source entries** (same file in multiple communities) — fixed: `apply.py` deduplicates by source before processing
4. **Import rewrite pointing at package dir, not file** — fixed: `_path_to_module` now reconstructs `dest_pkg / src_filename`
5. **Test files being proposed for moves** — fixed: planner skips files whose path contains any part in `_TEST_PARTS`
6. **Validation PYTHONPATH** — partially addressed: `do_validate` accepts `env` override; apply passes `PYTHONPATH=<wt_path>/src`; still failing

Current remaining failure: even with `PYTHONPATH` set to the worktree's `src/`, pytest can't find `pkg_000`. Likely cause: rope does NOT create `__init__.py` in destination package directories. The applicator needs to explicitly create `__init__.py` after each move.

## Design problems that need rethinking

### 1. Structural assumptions baked into the planner

The planner hardcodes `src/` and `lib/` as candidate source roots. Repos without these layouts will produce wrong target directories. `_detect_source_root` is a heuristic that misfires on flat layouts, monorepos, etc. The planner needs to either accept an explicit `source_root` parameter or detect layout from `pyproject.toml`/`setup.cfg`.

### 2. Test directory assumption

The `_TEST_PARTS` filter is a hardcoded set. Projects with differently-named test dirs (`spec/`, `t/`, `__tests__/`) will have test files swept into the refactor. Should derive from pytest config (`testpaths` in `pyproject.toml`) or be user-configurable.

### 3. Validation strategy is wrong for the structural phase

Running `pytest` after file moves will always fail because:
- The new `pkg_NNN` directories are not declared in `pyproject.toml`
- Editable install strict-mode finders only expose declared packages
- Tests import from the new names before those names are installable

Correct approach: split validation into two phases:
- **Structural check** (after file moves): `compileall` only
- **Behavioral check** (after package declaration + reinstall): `pytest`

Or: `pip install -e . --no-deps` in the worktree before running pytest.

### 4. Order of operations

Current (broken):
```
analyze → move files + rewrite imports (fused) → pytest
```

Correct:
```
analyze
→ move files only
→ compileall check
→ update pyproject.toml to declare new packages
→ pip install -e . in worktree
→ rewrite imports (separate pass)
→ compileall + pytest
```

File moves and import rewrites should be separable, independently triggerable phases.

### 5. Rope does not create `__init__.py`

`MoveModule` moves the file and rewrites imports in existing files. It does not create `__init__.py` in the destination. The applicator must explicitly create `__init__.py` in every new package directory after moves complete.

### 6. Contracts not wired in

`contracts/import_contracts.py` exists but is not part of any workflow. Intended design:
- After structural reorganization is validated, emit `import-linter` contract files enforcing the new boundaries
- Contract types: `forbidden`, `layers`, `independence`
- Needs a dedicated `generate_contracts` MCP tool
- Should run after the rename phase, not before

## Files changed this session (uncommitted)

- `src/refactor_plan/applicator/apply.py` — dedup + correct MoveRecord dest path
- `src/refactor_plan/applicator/worktree.py` — NEW: git worktree sandbox utilities
- `src/refactor_plan/mcp_server.py` — NEW: full MCP server with 11 tools
- `src/refactor_plan/planning/planner.py` — relative path resolution, skip `__init__.py`, skip test paths
- `src/refactor_plan/validation/validator.py` — accepts optional `env` dict
- `pyproject.toml` — removed anthropic, added mcp, added codescaffold-mcp entry point
- `src/refactor_plan/naming/namer.py` — removed all Anthropic API calls
- `src/refactor_plan/naming/docstringer.py` — removed all Anthropic API calls
- `src/refactor_plan/interface/cli.py` — removed name/docstring commands

## What needs to happen next

1. **Fix `__init__.py` creation** — after each file move, ensure destination dir has `__init__.py`
2. **Split validation phases** — structural (`compileall`) vs behavioral (`pytest`) with package reinstall in between
3. **Fix planner source root detection** — make it derive from project config, not hardcoded paths
4. **Separate file-move and import-rewrite phases** — independently triggerable
5. **Design and implement `generate_contracts`** — emit `import-linter` contracts from current structure
