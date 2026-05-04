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

## Current status (as of 2026-05-04)

The MCP server is registered as `codescaffold-mcp`.

Destructive tools use git worktree sandboxes under `/tmp/codescaffold_<ts>`. `sandbox=True` is the default. On success, changes are committed in the sandbox. On failure, the sandbox is discarded.

Current MCP tools:

```text
analyze            — rebuild graph, cluster, emit pending_decisions + STRUCTURE_REPORT
validate           — compileall + pytest
rollback           — undo last apply batch
approve_moves      — record model-approved file moves into the plan
apply              — execute approved moves in sandbox (phases: move → init → compile → import rewrite → pytest)
get_cluster_context — graph evidence per community for agent placement + naming decisions
apply_rename_map   — rename pkg_NNN placeholders on top of apply branch
rename             — ad-hoc symbol/module/package rename via rope
merge_sandbox      — merge final branch + auto-reset stale artifacts
discard_sandbox    — discard sandbox branch
reset              — manually clear stale plan, state, and .importlinter
get_symbol_context — graph context for a symbol (for docstring writing)
insert_docstring   — insert or replace a symbol docstring
contracts          — generate/refresh .importlinter contracts
validate_contracts — run import-linter against .importlinter
```

Anthropic API calls have been removed.

The bootstrap (running codescaffold on itself) has been partially completed:
- `name_apply.py` moved to `naming/`
- `worktree.py` moved to `interface/`
- `applicator/execution/` and `applicator/records/` subdirs created then promoted to `execution/` and `records/` at package root

Priorities 1–4 and Priority 7 from the original backlog are done. Priority 5 (graphify depth) and Priority 6 (contracts lifecycle) are partially done.

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

## Priority 1 — fix package creation after moves ✓ DONE

Implement explicit `__init__.py` creation for every destination package directory created by moves.

Requirements:

* create `__init__.py` in new package dirs
* create parent package `__init__.py` files for nested destinations
* do not overwrite existing files
* add tests for this behavior
* keep this independent from import rewriting

## Priority 2 — split apply into phases ✓ DONE

Refactor the apply pipeline so these are independently triggerable or internally staged:

```text
file move phase
package/init creation phase
import rewrite phase
validation phase
```

Do not keep “move files + rewrite imports + pytest” as one opaque operation.

A structural move should be able to run and validate with `compileall` before pytest is attempted.

## Priority 3 — fix validation semantics ✓ DONE

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

## Priority 4 — source root and test path detection ✓ DONE

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

## Priority 9 — agent placement guidance and status-quo bias

This is the most important outstanding problem.

### The trap

When an agent reviews `get_cluster_context` output, it sees the current directory layout alongside graph evidence. The current layout is visually salient. The agent tends to interpret co-location as correctness: "these files are already together, so they must belong together."

This is wrong. Co-location in an existing codebase is evidence of history, not of correct architecture. Files accumulate in directories because someone put them there at some point, not because they are structurally cohesive. The graph is the actual signal. The current layout is just context.

The tool currently reinforces this bias:

- `[confirmed: src/dir/]` is labelled as if approval has been given. It has not. It means "co-located as of today."
- Co-located communities with low cohesion and surprising cross-cluster connections are silently marked confirmed, receiving no scrutiny.
- The output shows current layout first, graph evidence second — the wrong priority order.

### Required fixes

**Reframe `[confirmed]` as `[co-located]`.**

Co-location is an observation, not a judgment. The label `[confirmed]` implies no action needed. Rename it.

**Show cohesion scores with interpretation.**

A cohesion score of 0.05 means the files in a community have almost no internal edge density — they are structurally independent, sharing a directory by accident or convention. Show this explicitly:

```
cohesion: 0.05  ← LOW — files share a directory but have weak structural coupling
cohesion: 0.62  ← HIGH — strong internal dependencies
```

Add a `[REVIEW]` flag for co-located communities where cohesion < 0.20 or where surprising connections are present. These are candidates for splitting even though they are not scattered.

**Show dependency direction, not just counts.**

"5 cross-cluster edges" is ambiguous. "5 outgoing (this community calls others)" vs "5 incoming (others call this community)" tells a different story. An inward-facing utility module should be placed near its callers, not near other utilities it happens to be grouped with.

Show:
```
Dependencies:
  → community 2: 8 outgoing (this calls those)
  ← community 0: 3 incoming (those call this)
```

**Show file role signals.**

For each file in a community, indicate:
- hub: high total degree (many connections across the codebase)
- bridge: connects two otherwise disconnected communities
- leaf: few connections, mostly consumed
- isolated: very few connections, may be misplaced or dead

**Explicit agent guidance at the top of every `get_cluster_context` response.**

Add a header block that the agent reads before reviewing communities:

```
PLACEMENT REVIEW GUIDANCE
─────────────────────────
The current directory layout reflects history, not architecture.
Co-located files are not confirmed as correctly placed.
Use graph signals — cohesion, dependency direction, surprising
connections — to evaluate placement, not current directory names.

Ask for each community:
  - Do these files actually call each other, or just share a folder?
  - Who depends on this community from outside?
  - Do surprising connections suggest this community is in the wrong place?
  - Would splitting this community reduce cross-cluster coupling?

[co-located] means "currently together" — not "correctly placed."
[PLACEMENT NEEDED] means "scattered" — but scattered may still be right.
```

**Cohesion floor for "no action needed" classification.**

Do not classify a community as placement-stable based on co-location alone. Require both:
- All files in the same directory AND
- Cohesion ≥ some threshold (e.g. 0.30) AND
- No surprising connections flagged

Only when all three hold should the output treat the community as low-priority.

### Why this matters

The whole point of the tool is to find structural problems the agent would not notice from reading code. If the agent then ignores graph signals in favour of the current layout, the tool is not working. The output must make graph signals salient and make status-quo bias difficult.

## Priority 10 — deeper graphify consumption in placement evidence

`get_cluster_context` currently shows:
- community_id, file list
- cohesion score
- cross-cluster edge counts
- surprising_connections list

It does not show:
- edge relation types (calls vs imports vs inherits vs shares_data — different architectural signals)
- edge confidence breakdown (EXTRACTED vs INFERRED — inferred edges should be labelled as such)
- which specific symbols drive the cross-cluster edges (file-level coupling is coarse; symbol-level coupling is actionable)
- god-node membership (a file with degree > threshold is a god node and may need splitting regardless of community)
- bridge score (a file that is the only connection between two communities is a structural risk)
- cycle involvement (files in import cycles must stay together or the cycle must be broken first)

These are all present in `view.G`. Surface them.

Do not add all of these at once. Add the highest-signal ones first:

1. Edge relation type breakdown (calls/imports/inherits) — tells you whether coupling is structural or just organizational
2. Symbol-level cross-cluster edges (top 3–5) — actionable, tells you exactly what drives the coupling
3. God-node flag — surfaces files that need splitting before moving
4. Bridge flag — surfaces files that are dangerous to move without analysis

## Anti-goals

Do not:

* silently stub missing functionality
* preserve broken behavior only because current tests expect it
* make graphify usage even shallower
* duplicate graphify’s graph server logic without a reason
* treat community detection as architectural truth
* treat co-location as evidence of correct placement
* treat graph edges as proof of semantic equivalence
* keep move/import rewrite/pytest fused
* rely on hidden source-root or test-path heuristics
* generate AI behavioral tests and treat them as proof of correctness
* create stale import-linter contracts
* permanently expose placeholder package names as final architecture
* use lazy imports to hide bad boundaries
* reintroduce Anthropic API calls into codescaffold internals
* present current directory layout as a neutral backdrop — it is a prior assumption that graph evidence should challenge

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


