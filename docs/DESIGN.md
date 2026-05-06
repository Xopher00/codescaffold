# Codescaffold Design Report: Graph-Driven Mechanical Refactoring with Graphify + Rope

## 1. Executive summary

`codescaffold` is already pointed in the right direction: it treats refactoring as a **planned, auditable, sandboxed workflow**, not as a sequence of free-form agent edits. The strongest design move is that the MCP server does **not** expose raw rope operations directly to the coding agent. Instead, it exposes higher-level steps such as analysis, approval, sandboxed apply, validation, and contract generation. That is the correct control boundary.

The current weakness is that `graphify` and `rope` are still connected too loosely. Graphify produces structural candidates; rope executes moves. But the bridge between them does not yet prove that a graph node corresponds to a rope-movable Python symbol, choose the safest rope operation, predict affected import/reference surfaces, or validate that the graph changed in the intended way after the move.

The target architecture should be:

```text
graphify structural evidence
  → graph node normalization
  → rope symbol/module resolution
  → operation planning
  → preflight checks
  → agent approval
  → sandboxed rope execution
  → compile/test/import-contract validation
  → graph-diff validation
  → audit record
```

The main design shift is this:

> Graphify should not merely propose “move X to Y.”
> Graphify should produce **typed refactoring intent**.
> Rope should execute the smallest mechanically safe operation that satisfies that intent.

---

## 2. Scope of this report

This report focuses on better ways to combine:

* `graphifyy` / `graphify`
* `rope-mcp-server`
* the current `codescaffold` orchestration layer

It deliberately does **not** focus on the known graph-direction/import-linter-contract issue, except where the proposed design affects graph projections and validation.

The reviewed `codescaffold` commit declares direct dependencies on `rope`, `rope-mcp-server`, `graphifyy`, `networkx`, `pydantic`, `libcst`, `import-linter`, `grimp`, and `mcp`; its project metadata currently says Python `>=3.11`. ([GitHub][1]) `rope-mcp-server` on PyPI requires Python `>=3.12`, which means the current dependency declaration can allow an invalid Python 3.11 environment. ([PyPI][2])

---

## 3. Current architecture

### 3.1 Dependency and tool shape

`codescaffold` is not merely “running two MCP servers.” It imports the graphify and rope packages as Python libraries and exposes its own curated MCP workflow. That is important: the project is already trying to become the **refactoring coordinator**, not just a proxy.

The graph side currently calls graphify extraction directly. `run_extract()` collects files, runs graphify extraction, builds a NetworkX graph, and wraps it in `GraphSnapshot`; the default is `directed=True`. ([GitHub][3])

The snapshot layer then clusters the graph, computes cohesion, and hashes graph structure for staleness detection. The hash currently includes sorted nodes and edge pairs, but not richer edge/node attributes such as relation type, source location, confidence, or import direction metadata. ([GitHub][4])

The rope side is a typed wrapper around `rope_mcp_server.refactoring`. It currently exposes wrappers for `move_symbol`, `rename_symbol`, `move_module`, `list_symbols`, and `close_rope_project`, parsing JSON returned by the upstream rope MCP package into typed results/errors. ([GitHub][5])

### 3.2 Existing MCP workflow

The current MCP tools already form a sensible high-level workflow:

```text
analyze
  → approve_moves
  → apply
  → validate
  → merge_sandbox / discard_sandbox
```

The `analyze()` tool runs graph extraction, proposes moves, saves a plan, and returns god nodes, cohesion scores, move candidates, and surprising connections. ([GitHub][6])

The `apply()` tool executes approved moves in a git worktree sandbox, applies rope operations, closes the rope project, commits the result in the sandbox, runs validation, and writes an audit record. ([GitHub][6])

This design is good. The issue is not the top-level workflow. The issue is the **semantic strength of the middle layer**: the path from graph evidence to mechanically executable rope operation is still too shallow.

---

## 4. Current graph-to-rope gap

The current candidate generator uses a relatively simple heuristic:

```text
low-cohesion community
  → node has many neighbours in another community
  → target community has dominant source_file
  → propose symbol move to that file
```

That is exactly what `propose_moves()` does: it looks for low-cohesion communities, checks whether a node’s neighbours mostly live in another community, chooses the dominant source file in that target community, and emits a `MoveCandidate(kind="symbol", source_file, symbol, target_file, ...)`. ([GitHub][7])

That is a useful first heuristic, but it conflates several different concepts:

| Graph concept                  | Rope concept              | Risk                                                                        |
| ------------------------------ | ------------------------- | --------------------------------------------------------------------------- |
| Node label                     | Python symbol name        | Label may not be a top-level class/function                                 |
| Node source file               | Rope source file          | Source file may be correct, but symbol may not be rope-resolvable           |
| Community pull                 | Refactor intent           | Pull does not determine operation type                                      |
| Target community dominant file | Destination file          | Dominant file may not be the safest destination                             |
| Graph edge                     | Import/reference relation | Edge may be semantic, inferred, undirected, or unrelated to import movement |

The missing component is a **resolver** that converts graph evidence into a rope-safe object:

```text
GraphNodeEvidence
  → RopeResolution
  → OperationPlan
```

Without that, graphify can tell the system “this node seems structurally misplaced,” but it cannot yet prove “this exact top-level class/function can be safely moved with rope from this file to that destination.”

---

## 5. Design thesis

The long-term design should treat the two tools as complementary, not interchangeable:

```text
Graphify answers:
  What belongs together?
  What is structurally misplaced?
  What depends on what?
  What communities/layers exist?
  What changed structurally after the refactor?

Rope answers:
  Can this Python symbol be resolved?
  Can this move/rename/module transformation be executed safely?
  Which files changed?
  Were imports/references mechanically updated?
```

So the core design principle is:

> Graphify should select and constrain refactoring intent.
> Rope should execute mechanically safe Python transformations.
> Codescaffold should mediate, validate, audit, and prevent unsafe agent improvisation.

---

## 6. Target architecture

### 6.1 Proposed pipeline

```text
1. Extract graph
   - directed import/reference graph
   - undirected cohesion/community graph

2. Generate graph evidence
   - communities
   - cross-community edges
   - god nodes
   - low-cohesion modules
   - candidate relocation pressure

3. Resolve graph nodes against rope
   - list top-level symbols in source file
   - match graph node to exact symbol
   - determine symbol kind
   - determine whether symbol is movable

4. Select operation
   - move_symbol
   - move_module
   - move_and_rename_module
   - convert_module_to_init
   - convert_module_to_package
   - rename_symbol
   - future extract_method / inline_variable

5. Preflight
   - destination exists or can be created
   - source symbol exists
   - import surface understood
   - contract impact predicted
   - graph direction projection consistent

6. Approval
   - agent sees evidence, not just a move command
   - agent approves typed plans, not raw edits

7. Apply in sandbox
   - perform rope operations
   - close rope project
   - commit sandbox state

8. Validate
   - compileall
   - pytest
   - import-linter
   - graph-diff against intent

9. Audit
   - before/after graph hashes
   - operation plan
   - rope changed files
   - graph delta
   - validation results
```

### 6.2 Proposed internal module layout

```text
src/codescaffold/bridge/
    evidence.py
    resolve.py
    operation_plan.py
    preflight.py
    graph_diff.py

src/codescaffold/operations/
    rope_ops.py
    rope_composite_ops.py

src/codescaffold/plans/
    schema.py
    operation_schema.py

src/codescaffold/graphify/
    extract.py
    snapshot.py
    projections.py
    evidence.py
```

The key new package is `bridge/`. That package should own the boundary between graph semantics and rope mechanics.

---

## 7. Proposed data model

### 7.1 Graph evidence

A move candidate should carry structured graph evidence, not just string reasons.

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class GraphNodeEvidence:
    node_id: str
    label: str
    source_file: str | None
    source_line: int | None
    source_col: int | None
    node_kind: str | None
    community_id: int
    degree: int
    internal_degree: int
    external_degree: int
    dominant_external_community: int | None
    dominant_external_ratio: float
    relation_types: tuple[str, ...]
    confidence: Literal["low", "medium", "high"]
```

### 7.2 Rope resolution

```python
@dataclass(frozen=True)
class RopeResolution:
    status: Literal[
        "resolved",
        "ambiguous",
        "not_found",
        "not_top_level",
        "not_python",
        "unsupported_kind",
    ]
    file_path: str
    symbol_name: str | None
    symbol_kind: Literal["class", "function", "variable", "module", "unknown"] | None
    line: int | None
    byte_offset: int | None
    candidates: tuple[str, ...] = ()
    reason: str | None = None
```

This is the critical safety layer. A graph node should not become a rope operation unless it resolves.

### 7.3 Operation plan

```python
@dataclass(frozen=True)
class OperationPlan:
    id: str
    intent: Literal[
        "relocate_symbol",
        "relocate_module",
        "split_module",
        "convert_module_to_package",
        "rename_symbol",
        "extract_helper",
    ]
    rope_operation: Literal[
        "move_symbol",
        "move_module",
        "move_and_rename_module",
        "convert_module_to_init",
        "convert_module_to_package",
        "rename_symbol",
        "extract_method",
        "inline_variable",
    ]
    source_file: str
    target_file: str | None
    target_folder: str | None
    symbol_name: str | None
    preflight_status: Literal["ready", "needs_review", "blocked"]
    evidence: GraphNodeEvidence
    resolution: RopeResolution
    expected_changed_files: tuple[str, ...] | None = None
```

The approval step should approve `OperationPlan`s, not raw move dictionaries.

---

## 8. Operation-selection matrix

The most important design improvement is to stop treating all graph tension as `move_symbol`.

`rope-mcp-server` exposes more operations than `codescaffold` currently wraps: move symbol, move module, move-and-rename module, convert module to init, convert module to package, rename symbol, extract method, inline variable, and list symbols. ([PyPI][2])

Use graph evidence to choose among them:

| Graph evidence                                                         | Preferred operation         | Why                                           |
| ---------------------------------------------------------------------- | --------------------------- | --------------------------------------------- |
| Top-level class/function is strongly pulled toward another module      | `move_symbol`               | Smallest safe semantic move                   |
| Entire file’s symbols belong mostly to another package/community       | `move_module`               | Better than many individual moves             |
| `foo_extra.py`, `foo_mixins.py`, `foo_helpers.py` belongs under `foo/` | `move_and_rename_module`    | Handles “sibling file into package” refactors |
| Large `foo.py` should become package root before splitting             | `convert_module_to_init`    | Preserves public import path                  |
| Module content should move into `foo/foo.py` and imports should update | `convert_module_to_package` | Useful when `__init__.py` should stay thin    |
| Symbol has right location but poor name                                | `rename_symbol`             | No structural move required                   |
| Internal repeated block should become helper                           | `extract_method`            | Future graph-local cleanup                    |
| Temporary/local alias creates noise                                    | `inline_variable`           | Future cleanup/refinement                     |

This unlocks much better graphify + rope composition. Graphify determines the architectural smell; rope performs the precise mechanical edit.

---

## 9. Missing high-value wrappers

### 9.1 `convert_module_to_init`

This is probably the most important missing wrapper for your use case. The upstream rope MCP package explicitly describes `convert_module_to_init` as transforming `foo.py` into `foo/__init__.py` without changing imports, and recommends it as a way to convert a module to a package before moving related files into that package. ([PyPI][2])

That maps directly to the “organic file grew too large, now split it mechanically” problem.

Recommended wrapper:

```python
def convert_module_to_init(project_path: str, module_path: str) -> RopeChangeResult:
    ...
```

Then expose it only through a higher-level plan:

```text
split_module_scaffold
  → convert_module_to_init
  → move related symbols/files into submodules
  → preserve public imports
```

### 9.2 `move_and_rename_module`

The upstream package specifically supports moving a module into a folder and optionally renaming it, such as `foo_extra.py` → `foo/extra.py`; it also includes a workaround for a Rope bug involving files that import from both the destination package and the module being moved. ([PyPI][2])

That is highly relevant for graph-driven cleanup because graphify will often identify sibling helper/mixin/extra files that belong under a package.

Recommended wrapper:

```python
def move_and_rename_module(
    project_path: str,
    module_path: str,
    dest_folder: str,
    new_name: str | None = None,
) -> RopeChangeResult:
    ...
```

### 9.3 `convert_module_to_package`

This should be available, but lower priority than `convert_module_to_init`. Use it when the desired final shape is:

```text
foo/
  __init__.py
  foo.py
```

rather than:

```text
foo/
  __init__.py   # formerly foo.py
```

The upstream package describes `convert_module_to_package` as transforming `foo.py` into `foo/foo.py` while updating imports project-wide. ([PyPI][2])

---

## 10. Proposed composite operations

### 10.1 `preflight_candidate`

Add a preflight stage between `analyze` and `approve_moves`.

```text
analyze
  → preflight_candidates
  → approve_operation_plans
  → apply
```

Responsibilities:

1. Read graph candidate.
2. Use rope `list_symbols(source_file)`.
3. Match graph label to exact top-level symbol.
4. Determine whether the graph candidate is:

   * rope-ready
   * ambiguous
   * blocked
   * better represented as module move
   * better represented as package conversion
5. Attach resolution evidence.

Output example:

```json
{
  "candidate_id": "cand_007",
  "status": "ready",
  "intent": "relocate_symbol",
  "operation": "move_symbol",
  "source_file": "src/codescaffold/foo.py",
  "symbol": "PlanBuilder",
  "target_file": "src/codescaffold/plans/builder.py",
  "rope_resolution": {
    "status": "resolved",
    "symbol_kind": "class",
    "line": 42
  },
  "graph_evidence": {
    "community_id": 3,
    "dominant_external_community": 7,
    "dominant_external_ratio": 0.82
  }
}
```

### 10.2 `split_module_by_graph_clusters`

This is the highest-value future workflow.

```python
def split_module_by_graph_clusters(
    repo_path: str,
    module_path: str,
    cluster_to_new_module: dict[int, str],
    export_policy: str = "preserve_public_api",
) -> str:
    ...
```

Internal flow:

```text
1. Use graphify to find symbol clusters inside a large module.
2. Use rope list_symbols to resolve movable top-level symbols.
3. Convert module.py to module/__init__.py.
4. Create target submodules.
5. Move clustered symbols into submodules.
6. Update __init__.py exports if preserving public API.
7. Run compile/test/import-linter.
8. Rebuild graph.
9. Verify graph delta matches intended split.
```

This is where `codescaffold` can become genuinely useful beyond “candidate move tool.” It would automate the mechanical restructuring agents are bad at.

### 10.3 `move_package_family`

This workflow handles cases like:

```text
customer_order.py
customer_order_mixins.py
customer_order_extra.py
customer_order_tests.py
```

Target:

```text
customer_order/
  __init__.py
  mixins.py
  extra.py
  tests.py
```

Flow:

```text
convert_module_to_init(customer_order.py)
move_and_rename_module(customer_order_mixins.py → customer_order/mixins.py)
move_and_rename_module(customer_order_extra.py → customer_order/extra.py)
validate
graph-diff
```

This is a perfect graphify + rope combination: graphify identifies the file family; rope performs safe module moves and import rewrites.

---

## 11. Graph projections: do not use one graph for every purpose

You should explicitly maintain at least two graph projections.

### 11.1 Cohesion projection

Purpose:

```text
What belongs together?
Which symbols/files cluster together?
Which nodes are structurally misplaced?
```

This projection can be undirected or symmetrized. It is suitable for:

* community detection
* cohesion scoring
* god-node analysis
* candidate generation
* surprising connections

### 11.2 Dependency/import projection

Purpose:

```text
Who imports whom?
Which package depends on which?
What layer direction is allowed?
Which move would violate contracts?
```

This projection must preserve direction. It is suitable for:

* import-linter contract generation
* cycle detection
* layer derivation
* dependency impact analysis
* post-move regression checks

Graphify supports a directed mode in its command reference, and the PyPI docs list `/graphify ./raw --directed` as an option to preserve edge direction. ([PyPI][8]) `codescaffold` already defaults `run_extract()` to building a directed graph, so the next design step is not merely “directed or undirected”; it is maintaining **separate semantic projections** for separate jobs. ([GitHub][3])

---

## 12. Graph-diff validation

Current validation is mostly downstream correctness:

```text
compileall
pytest
import-linter
```

That is necessary, but insufficient for graph-driven refactoring. A move can compile and still fail the architectural intent.

Add graph-diff validation:

```text
before_graph
  + operation_plan
  + after_graph
  → structural audit
```

The graph-diff should answer:

| Question                                                | Why it matters                         |
| ------------------------------------------------------- | -------------------------------------- |
| Did the intended node move to the intended file/module? | Confirms semantic target               |
| Did cross-community edges decrease?                     | Confirms cohesion improved             |
| Did new forbidden edges appear?                         | Catches architecture regressions       |
| Did cycles appear/disappear?                            | Supports import-linter contract health |
| Did a god node become worse?                            | Avoids centralizing too much           |
| Did changed files match expected surface?               | Detects surprise edits                 |
| Did graph hash change in expected way?                  | Gives durable audit evidence           |

Current `GraphSnapshot` hashes only node/edge structure, which is enough for basic staleness but not enough for rich graph-diff auditing. ([GitHub][4]) Consider adding a second hash:

```text
structural_hash:
  nodes + edges only

semantic_hash:
  nodes + edges + selected node/edge attributes
```

Recommended graph-diff output:

```json
{
  "candidate_id": "cand_007",
  "before_hash": "abc123",
  "after_hash": "def456",
  "intended": {
    "symbol": "PlanBuilder",
    "from": "src/foo.py",
    "to": "src/plans/builder.py"
  },
  "observed": {
    "symbol_found": true,
    "new_source_file": "src/plans/builder.py"
  },
  "cohesion_delta": {
    "source_community": -0.03,
    "target_community": 0.08
  },
  "new_cycles": [],
  "new_forbidden_edges": [],
  "unexpected_changed_files": []
}
```

---

## 13. MCP surface redesign

The current MCP surface is already appropriately high-level. Keep that principle.

Do **not** expose raw rope tools as primary tools. Expose refactor workflow tools.

Recommended future MCP tools:

```text
analyze(repo_path)
get_cluster_context(repo_path, community_id)
preflight_candidates(repo_path)
explain_candidate(repo_path, candidate_id)
approve_operation_plans(repo_path, plan_ids)
apply(branch_name, repo_path)
validate(branch_name, repo_path)
graph_diff(branch_name, repo_path)
update_contract(branch_name, repo_path)
merge_sandbox(branch_name, repo_path)
discard_sandbox(branch_name, repo_path)
```

Graphify itself exposes direct graph-query access through MCP tools such as `query_graph`, `get_node`, `get_neighbors`, and `shortest_path`. ([PyPI][8]) `codescaffold` should borrow the concept, but specialize it for refactoring:

```text
get_candidate_subgraph(candidate_id)
get_symbol_neighbors(symbol)
get_move_impact(candidate_id)
get_package_dependency_path(source, target)
get_contract_impact(candidate_id)
```

That gives the coding agent enough context to reason, while still preventing it from performing arbitrary filesystem edits.

---

## 14. Approval model

Approval should not mean “approve this move dictionary.”

Current approval accepts move dictionaries with fields such as `kind`, `source_file`, `target_file`, and `symbol`. ([GitHub][6]) That is too low-level once more operation types exist.

Approval should mean:

```text
Approve this preflighted operation plan.
```

A plan should already include:

* graph evidence
* rope resolution
* chosen operation
* alternatives considered
* preflight status
* expected changed files if predictable
* validation expectations
* contract impact

Approval UI/output should show:

```text
Candidate C-12: relocate_symbol

Graph evidence:
- Symbol `FooBuilder` is in low-cohesion community 4.
- 82% of its external neighbours point to community 8.
- Target community dominant file: `src/foo/builders.py`.

Rope resolution:
- Resolved as top-level class in `src/foo/core.py`, line 41.
- Operation selected: move_symbol.

Risk:
- Medium: target file already imports from source package.
- No package cycle predicted.

Approve with:
approve_operation_plans(["C-12"])
```

---

## 15. Error feedback loop

Rope errors should feed back into candidate repair.

Current `rope_ops` converts upstream errors into typed exceptions, which is good. ([GitHub][5]) The next layer should classify failures into repair strategies:

| Rope failure             | Follow-up                                                           |
| ------------------------ | ------------------------------------------------------------------- |
| Symbol not found         | Run `list_symbols`, show closest symbols, mark candidate unresolved |
| Destination missing      | Decide whether to create file/package or block                      |
| Refactoring error        | Try smaller operation or require agent review                       |
| Module/package conflict  | Consider `convert_module_to_init` or `move_and_rename_module`       |
| Import conflict          | Run graph import-impact check                                       |
| Unexpected changed files | Block merge until graph-diff/audit review                           |

This turns rope failures into structured feedback rather than dead ends.

---

## 16. Versioning and dependency risks

### 16.1 Python version mismatch

`codescaffold` currently says Python `>=3.11`, while `rope-mcp-server` says Python `>=3.12`. ([GitHub][1]) Fix this before building more functionality.

Options:

1. Raise `codescaffold` to Python `>=3.12`.
2. Remove direct dependency on `rope-mcp-server` and use `rope` directly.
3. Vendor/adapt only the needed rope wrapper functions with your own Python-version policy.

Best practical choice: **raise `codescaffold` to Python `>=3.12`** unless you have a hard 3.11 compatibility requirement.

### 16.2 Unpinned `graphifyy`

`graphifyy` is currently at version `0.7.7`, released May 5, 2026, and PyPI shows many releases in the immediately preceding days. ([PyPI][8]) That is a fast-moving dependency. `codescaffold` currently depends on unpinned `"graphifyy"`. ([GitHub][1])

Recommended:

```toml
graphifyy>=0.7.7,<0.8
```

or, for tighter control during the refactor:

```toml
graphifyy==0.7.7
```

Then add compatibility tests around:

* `collect_files`
* `extract`
* `build_from_json`
* node attribute schema
* edge attribute schema
* directed graph behavior
* clustering assumptions

---

## 17. Implementation roadmap

### Phase 1 — Stabilize dependency and bridge layer

Goal: make graph candidates mechanically checkable.

Tasks:

```text
1. Fix Python requirement.
2. Pin graphifyy.
3. Add bridge/evidence.py.
4. Add bridge/resolve.py.
5. Add preflight_candidates.
6. Modify plan schema to store resolution state.
```

Deliverable:

```text
analyze → preflight_candidates → approve_operation_plans
```

No new rope operations yet. Just make existing `move_symbol` and `move_module` safer.

### Phase 2 — Expand rope operation coverage

Goal: stop forcing all structural smells into symbol moves.

Tasks:

```text
1. Wrap convert_module_to_init.
2. Wrap move_and_rename_module.
3. Wrap convert_module_to_package.
4. Add operation-selection matrix.
5. Add operation alternatives in candidate output.
```

Deliverable:

```text
Candidate can become:
- move_symbol
- move_module
- convert_module_to_init + move_symbol
- convert_module_to_init + move_and_rename_module
```

### Phase 3 — Add graph-diff validation

Goal: verify architectural intent, not just Python correctness.

Tasks:

```text
1. Capture before graph in plan.
2. Rebuild graph after sandbox apply.
3. Compare node locations, edge deltas, community deltas.
4. Add graph_diff audit file.
5. Include graph-diff summary in apply output.
```

Deliverable:

```text
apply result includes:
- rope changed files
- compile/test results
- import contract result
- graph-diff result
```

### Phase 4 — Add composite refactoring workflows

Goal: automate high-value mechanical reorganizations.

Tasks:

```text
1. split_module_by_graph_clusters.
2. move_package_family.
3. preserve_public_api export policy.
4. LibCST __init__.py export management.
5. rollback/partial-failure handling.
```

Deliverable:

```text
Graph-driven module/package split with auditable rope operations.
```

### Phase 5 — Contract-aware planning

Goal: avoid producing moves that are likely to violate import-linter contracts.

Tasks:

```text
1. Use directed dependency projection.
2. Predict package DAG impact before apply.
3. Mark moves as contract-safe / contract-risky / contract-breaking.
4. Offer alternative destinations before sandbox execution.
```

Deliverable:

```text
approve_operation_plans only defaults to contract-safe candidates.
```

---

## 18. Success criteria

The design is working when `codescaffold` can reliably answer these before touching files:

```text
Can the graph node be resolved to a rope symbol or module?
Which rope operation is safest?
What files are likely to change?
What package/layer edges may change?
Could this introduce an import cycle?
Is the candidate a symbol move, module move, package split, or rename?
```

And these after applying changes:

```text
Did rope make the intended mechanical change?
Did imports still compile?
Did tests pass?
Did import contracts pass or change intentionally?
Did the graph improve according to the candidate’s intent?
Was the change surface expected?
Can the audit explain what happened?
```

---

## 19. Recommended next concrete change

The next best implementation step is **not** another candidate heuristic.

The next best step is:

```text
Add graph-to-rope preflight resolution.
```

Minimal implementation:

```text
bridge/resolve.py
  resolve_symbol_candidate(candidate, repo_path) -> RopeResolution

bridge/preflight.py
  preflight_candidate(candidate, repo_path) -> OperationPlan
```

Use existing `list_symbols()` first. Do not add more graph intelligence until every graph-derived candidate is classified as:

```text
ready
needs_review
blocked
```

That one change will make the whole system safer because it turns graphify output from “interesting suggestion” into “mechanically executable plan candidate.”

---

## 20. Final assessment

`codescaffold` should evolve from:

```text
graphify proposes → rope moves
```

into:

```text
graphify explains architecture
  → codescaffold resolves/refines intent
  → rope executes safe transformations
  → graphify verifies structural outcome
```

That is the core design.

The project is strongest where it already avoids raw agent editing: sandboxing, plans, validation, and audit records. The next maturity jump is to make the graph/rope bridge explicit, typed, and preflighted. Once that exists, higher-level workflows like package-family moves and graph-clustered module splits become natural instead of fragile.

[1]: https://raw.githubusercontent.com/Xopher00/codescaffold/3b1d630d939266acebd0bc816f9e2e48cda60757/pyproject.toml "raw.githubusercontent.com"
[2]: https://pypi.org/project/rope-mcp-server/ "rope-mcp-server · PyPI"
[3]: https://raw.githubusercontent.com/Xopher00/codescaffold/3b1d630d939266acebd0bc816f9e2e48cda60757/src/codescaffold/graphify/extract.py "raw.githubusercontent.com"
[4]: https://raw.githubusercontent.com/Xopher00/codescaffold/3b1d630d939266acebd0bc816f9e2e48cda60757/src/codescaffold/graphify/snapshot.py "raw.githubusercontent.com"
[5]: https://raw.githubusercontent.com/Xopher00/codescaffold/3b1d630d939266acebd0bc816f9e2e48cda60757/src/codescaffold/operations/rope_ops.py "raw.githubusercontent.com"
[6]: https://raw.githubusercontent.com/Xopher00/codescaffold/3b1d630d939266acebd0bc816f9e2e48cda60757/src/codescaffold/mcp/tools.py "raw.githubusercontent.com"
[7]: https://raw.githubusercontent.com/Xopher00/codescaffold/3b1d630d939266acebd0bc816f9e2e48cda60757/src/codescaffold/candidates/propose.py "raw.githubusercontent.com"
[8]: https://pypi.org/project/graphifyy/ "graphifyy · PyPI"
