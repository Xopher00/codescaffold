# Decisions

## D1 — Typed exceptions for Rope errors

**Decision:** `codescaffold.operations` raises `RopeRefactoringError`, `RopeArgumentError`, or `RopeUnexpectedError` (all subclasses of `RopeOperationError`) instead of returning a `Result` type.

**Rationale:** The rope→codescaffold boundary is crossed exactly once, inside `_unwrap()`. All callers are internal orchestration code that propagates failures upward. Exception types are the right tool; `Result` types add noise without benefit in a linear pipeline.

---

## D2 — Fresh candidate logic, no port from graphify_adapter

**Decision:** `codescaffold.candidates.propose` was written from scratch. The legacy `refactor_plan/interface/graphify_adapter.py` was not ported.

**Rationale:** The legacy adapter used private graphify symbols (`_is_file_node`, `_is_concept_node`) and mixed graph extraction with candidate logic. Starting fresh let us establish a clean contract (`GraphSnapshot → list[MoveCandidate]`) and avoid dependency on private graphify internals.

---

## D3 — 8-tool MCP surface; rope and graphify primitives are internal

**Decision:** The MCP server exposes exactly 8 tools: `analyze`, `get_cluster_context`, `approve_moves`, `apply`, `validate`, `merge_sandbox`, `discard_sandbox`, `reset`. No rope operations (`move_symbol`, `rename_symbol`, etc.) and no raw graphify functions are registered.

**Rationale:** Direct exposure of rope primitives would let agents bypass plan persistence and sandboxing. Direct exposure of graphify would produce raw graph dumps. The 8-tool surface enforces "no mutation outside a sandbox, no execution without a persisted plan."

---

## D4 — No private graphify symbols

**Decision:** `codescaffold.graphify` does not import any `_`-prefixed symbol from graphify.

**Rationale:** The legacy codebase depended on `graphify.analyze._is_file_node` and `_is_concept_node`. These are not part of the public API and could change without notice. All analysis in codescaffold goes through graphify's public API (`god_nodes`, `surprising_connections`, `score_all`, `cluster`).

---

## D5 — sha256 over sorted (nodes, edges) as the staleness signal

**Decision:** `GraphSnapshot.graph_hash` is `sha256(json(sorted_nodes + sorted_edges))`. `assert_fresh()` compares this hash to detect repo changes between `analyze` and `approve_moves`.

**Rationale:** Simple, deterministic, and reproducible. Node/edge identity captures structural change. Attribute changes (e.g. docstring edits) that don't affect the import graph do not invalidate the plan — which is the right behavior since candidates are based on structure, not content.

---

## D6 — GraphSnapshot is directed by default

**Decision:** `run_extract` passes `directed=True` to `build_from_json`. `_hash_graph` uses `(u, v)` pairs (not `(min, max)`) for directed graphs so flipping an import direction bumps the hash.

**Rationale:** Directed edges are essential for cycle detection and topological layer computation. Undirected graphs erase import direction, making it impossible to tell which package imports which. `graphify.cluster` auto-converts DiGraph→undirected internally, so community detection is unaffected.

---

## D7 — Contract generation refuses on package cycles

**Decision:** `generate_importlinter_config` returns `written=False` and populates `cycles_detected` when the package graph has cycles. It never writes `.importlinter` over a cyclic graph.

**Rationale:** Import-linter `layers` contracts are only meaningful over a DAG. Writing them for a cyclic graph would produce contracts that immediately fail, mislead the agent, and waste iteration. Returning `MoveCandidate`s for cycle-breaking flows the fix into the existing `approve_moves → apply` pipeline.

---

## D8 — Contract violation surfaces both update and counterpropose paths

**Decision:** When `apply` detects a contract regression (`pre_apply_passed=True` and `contracts_ok=False`), it surfaces both recovery paths in the audit: `update_contract` (accept the new structure) and `propose_violation_fix` (find alternative move targets). The apply still completes; the sandbox is not discarded automatically.

**Rationale:** The agent needs information to decide. Blocking the apply and forcing discard would lose the sandbox state needed to inspect violations. Giving both paths lets the agent choose the right one based on intent.
