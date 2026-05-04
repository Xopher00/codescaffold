# Claude Code Prompt — codescaffold bootstrap stabilization

You are working in `Xopher00/codescaffold`.

Read the code before changing anything. Do not work from this prompt alone.

Start with:

- `STATUS.md`
- `CLAUDE.md`
- `pyproject.toml`
- `demo.py`
- `src/refactor_plan/mcp_server.py`
- `src/refactor_plan/interface/graph_bridge.py`
- `src/refactor_plan/interface/cluster_view.py`
- `src/refactor_plan/planning/planner.py`
- `src/refactor_plan/applicator/`
- `src/refactor_plan/contracts/`
- `src/refactor_plan/validation/`
- `src/refactor_plan/naming/`
- existing tests

Also review graphify’s MCP/server interface, especially `graphify/serve.py`, because codescaffold currently appears to use graphify too shallowly.

## Current status

The MCP server is registered as `codescaffold-mcp`.

Destructive tools use git worktree sandboxes under `/tmp/codescaffold_<ts>`. `sandbox=True` is the default. On success, changes are committed in the sandbox. On failure, the sandbox can be discarded.

Current MCP tools:

```text
analyze
validate
rollback
apply
get_cluster_context
apply_rename_map
rename
merge_sandbox
discard_sandbox
get_symbol_context
insert_docstring
````

Anthropic API calls have been removed.

The bootstrap attempt runs codescaffold on itself. `apply` reaches validation, passes `compileall`, then fails pytest with:

```text
ModuleNotFoundError: No module named 'pkg_000'
```

Known fixes already made:

```text
relative graph-node paths resolved against repo_root
planner skips __init__.py
apply deduplicates duplicate source entries
_path_to_module reconstructs dest_pkg / src_filename
planner skips hardcoded test-path parts
validation accepts env override
apply passes PYTHONPATH=<worktree>/src
```

Likely remaining immediate cause:

```text
rope MoveModule does not create __init__.py in new destination packages
```

The applicator must explicitly create package `__init__.py` files after moves.

## Primary goal

Stabilize the structural refactoring pipeline before adding speculative features.

Do not try to build a fully automatic architecture-rewriting system in this pass.

The current problem is that file moves, import rewrites, package creation, validation, and pytest are too tightly fused. Split the pipeline into explicit phases with reviewable artifacts.

Target pipeline:

```text
analyze
→ generate/refresh proposed contracts from current graph
→ plan moves
→ move files only
→ create required __init__.py files
→ structural validation: compileall
→ update package/install metadata if required
→ reinstall in sandbox if required: pip install -e . --no-deps
→ rewrite imports as a separate phase
→ compileall again
→ behavioral validation: pytest
→ refresh contracts after final names/moves
```

Contracts should exist early enough to protect latent structure, but they must also be refreshable after moves and renames. Treat contracts as generated, maintainable artifacts, not one-time static files.

## Priority 1 — fix package creation after moves

Implement explicit `__init__.py` creation for every destination package directory created by moves.

Requirements:

* create `__init__.py` in new package dirs
* create parent package `__init__.py` files for nested destinations
* do not overwrite existing files
* add tests for this behavior
* keep this independent from import rewriting

## Priority 2 — split apply into phases

Refactor the apply pipeline so these are independently triggerable or internally staged:

```text
file move phase
package/init creation phase
import rewrite phase
validation phase
```

Do not keep “move files + rewrite imports + pytest” as one opaque operation.

A structural move should be able to run and validate with `compileall` before pytest is attempted.

## Priority 3 — fix validation semantics

Validation needs at least two modes:

```text
structural:
    compileall
    syntax/import-shape checks where safe
    no pytest requirement

behavioral:
    project installability
    import rewrite completed
    pytest when available
```

Do not assume every target repo has tests. If no tests are present, validation should still provide useful checks:

```text
compileall
import smoke checks for discovered packages/modules
entry point import checks
optional generated smoke tests
```

Do not generate AI-written behavioral unit tests and treat them as a correctness oracle. Generated tests may be useful as smoke/characterization scaffolding, but they must be marked as generated and reviewable.

## Priority 4 — source root and test path detection

The planner currently hardcodes source roots such as `src/` and `lib/`. Replace this with explicit configuration plus safer detection.

Support:

```text
explicit source_root parameter/config
pyproject.toml package-dir / setuptools config
pytest config
flat layouts
src layouts
fallback heuristic only when visible and logged
```

Test path detection should not rely only on `_TEST_PARTS`.

Derive test paths where possible from:

```text
pyproject.toml
pytest.ini
setup.cfg
tox.ini
```

Keep fallback heuristics, but make them explicit, testable, and overrideable.

## Priority 5 — consume graphify more deeply

Do not treat graphify as only:

```text
community_id → files
```

Review `graphify/serve.py` and compare what graphify already exposes with how codescaffold currently uses it.

Graphify MCP already supports graph-level operations such as:

```text
BFS/DFS graph search
context-filtered traversal
node lookup
neighbor lookup
community lookup
god/high-degree node discovery
graph statistics
confidence summaries
shortest paths
relation/context-aware edges
```

Codescaffold currently appears to reduce graphify mostly into `file_communities`, even though `view.G` preserves the full graph.

Improve existing behavior by using graph data already available in `view.G`:

```text
incoming/outgoing dependency summaries
internal vs external edge density
bridge-node detection
high-degree/god-node detection
relation type summaries
edge confidence summaries
cluster risk scoring
shortest-path explanations between clusters/symbols
clearer rationale in STRUCTURE_REPORT.md
```

Do not create a separate graph analysis system until codescaffold is properly consuming graphify’s existing node, edge, relation, confidence, community, neighbor, and path data.

Consider whether codescaffold should mirror graphify MCP query capabilities internally, delegate to graphify’s MCP server, or expose compatible graph-query tools in its own MCP server. Do not merge code blindly. Decide based on maintainability and boundary clarity.

## Priority 6 — generate and maintain import-linter contracts

`contracts/import_contracts.py` exists but is not wired into a real workflow.

Add a real `generate_contracts` tool or equivalent command path.

It should emit import-linter-compatible contracts from graph-derived structure.

Support at least:

```text
forbidden imports
layers
independence
```

Important: contracts need lifecycle management.

Design for:

```text
generate baseline contracts from current graph
mark generated contracts with provenance
validate whether contracts are stale
refresh contracts after moves/renames
avoid hand-edited generated files being silently overwritten
```

Contracts should help preserve latent structure before major refactors, but after file/symbol moves and renames, contracts must be updated to reflect the new module names.

Do not make contracts depend permanently on placeholder names like `pkg_000`.

## Priority 7 — discrete moves and rename workflow ✓ DONE

File moves and symbol moves should be discrete and reviewable.

Current placeholder names such as:

```text
pkg_NNN
mod_NNN.py
```

are acceptable as temporary structural placeholders only if the rename workflow is clear.

Add or improve workflow support so that after structural moves, the system can produce a targeted rename request/report for the moved packages, files, and symbols.

The rename phase should:

```text
show exactly what needs naming
include graph-derived context for each item
support apply_rename_map
update imports safely
refresh contracts afterward
```

Do not require the user or agent to infer all rename targets manually from a giant diff.

## Priority 8 — inspect demo.py

Check whether `demo.py` is stale relative to the current MCP-first workflow and sandbox/apply pipeline.

Either:

```text
update it
replace it with a current minimal demo
move it into examples/
or mark/remove it if it is misleading
```

Do not leave a stale demo that teaches the wrong usage pattern.

## Later design only — duplicate logic / symbol equivalence

Do not implement destructive consolidation in this pass.

However, assess where a future read-only equivalence report would fit.

The future purpose is to identify when functions or symbols in different modules may implement repeated logic.

Graphify can provide structural evidence:

```text
files
symbols
calls
imports
clusters
source locations
neighbor context
shortest paths
edge confidence
```

But graph structure is not proof of semantic equivalence.

A future codescaffold-owned report could use:

```text
normalized AST/CST body
alpha-renamed locals
signature similarity
call-neighborhood overlap
import overlap
literal/control-flow shape
docstring/name similarity
side-effect markers
graph cluster relation
```

Possible output artifacts:

```text
.refactor_plan/equivalence_report.json
.refactor_plan/EQUIVALENCE_REPORT.md
.refactor_plan/canonical_symbol_proposals.json
```

Candidate categories:

```text
exact_duplicate
renamed_duplicate
near_duplicate
wrapper_or_delegate
parallel_implementation
same_shape_different_behavior
accidental_similarity
```

For now, only design the integration point. Do not wire automatic merge/consolidation.

## Additional focus: generated tests as validation scaffolding

Explore generated tests, but do not treat generated behavioral tests as a correctness oracle.

`unittest` is acceptable as the baseline because it is in the Python standard library and supports discovery through `python -m unittest discover`. Use it first for deterministic smoke tests, especially importability checks after file moves, package creation, and import rewrites.

Add a generated smoke-test phase that can verify:

- discovered packages import successfully
- moved modules import under their new names
- public entry points still import
- MCP server module imports
- generated package directories are importable

Generated smoke tests may block validation because they check mechanical integrity.

For behavioral tests, distinguish:

- existing project tests: authoritative
- generated characterization tests: review-gated
- AI-written intent tests: advisory until reviewed

Do not generate AI behavioral tests and treat them as proof that a refactor is valid. If generated tests are created, place them in a clearly marked generated-test location and report them separately.

Investigate whether tools such as Pynguin, Hypothesis, or CrossHair are useful:

- Pynguin for automated characterization/unit-test generation
- Hypothesis for property-based tests where invariants are known
- CrossHair for small typed/contracted pure functions

For this pass, prioritize deterministic import/smoke tests over generated behavioral tests.

## Anti-goals

Do not:

* silently stub missing functionality
* preserve broken behavior only because current tests expect it
* make graphify usage even shallower
* duplicate graphify’s graph server logic without a reason
* treat community detection as architectural truth
* treat graph edges as proof of semantic equivalence
* keep move/import rewrite/pytest fused
* rely on hidden source-root or test-path heuristics
* generate AI behavioral tests and treat them as proof of correctness
* create stale import-linter contracts
* permanently expose placeholder package names as final architecture
* use lazy imports to hide bad boundaries
* reintroduce Anthropic API calls into codescaffold internals

## Expected output

At the end, provide:

```text
1. what changed
2. which pipeline phase was stabilized
3. which graphify data codescaffold now consumes better
4. whether demo.py was updated/removed/kept and why
5. what tests were added or updated
6. what still fails, with exact commands and errors
7. next smallest safe step
```


