# Codescaffold Tool UX Improvements — Spec

## Overview

The staged workflow (analyze → approve → apply → merge) and the sandbox validation loop are sound and should be preserved unchanged. The improvements below add signal *within* the existing workflow rather than restructuring it. The goal is to reduce the amount of reasoning the LLM must do over raw graph data, and to surface blockers before apply rather than after.

---

## Key Themes

### 1. Circular Import Pre-Flight (Highest Priority)

**Problem:** The circular import risk caused actual failed applies and required post-hoc debugging. Both risk forms exist:
- *Direct self-import*: the symbol being moved already imports from its destination file.
- *Carry-over cycle*: the imports the symbol carries along create a transitive cycle through the destination.

Both forms produce `ImportError: cannot import name X from partially initialized module` at runtime. Neither is currently detected before apply.

**Fix:** Bake the check into `approve_symbol_moves`. Before recording any approval, the tool should:
1. Parse the symbol's body (already done by `_collect_symbol_names`) and identify its import dependencies.
2. Compute the destination module name.
3. Check if any dependency module equals the destination (direct self-import).
4. Check if the destination module transitively imports the source module (carry-over cycle).

If either condition holds, `approve_symbol_moves` should **refuse** with a clear explanation rather than recording the approval. This is a hard block — both forms are blockers.

**Why here:** The model currently can't detect this from tool output alone. Baking it into `approve_symbol_moves` keeps the workflow single-step (no new tool call), and the refusal message gives the model the information it needs to pick an alternative destination.

---

### 2. Hub-File Filtering + Proposal Rationale

**Problem:** The planner proposed `mcp_server.py`, `demo.py`, and `cleanup.py` as symbol move destinations based on graph edge proximity. All three are hub files or utility files — wrong architecturally even when graph distance is short. The model had to override every one of these, re-deriving the correct destination from scratch each time.

**Fix (two parts):**

*Filter:* Any file above a configurable edge-count threshold (e.g., top 10% of the graph by edge count — the god nodes) should be excluded as a symbol move destination by the planner. The god-node list is already computed during analysis (`god_nodes` in the plan); use it as a denylist.

*Rationale:* For the remaining proposals, surface the top 1–2 graph reasons the planner chose that destination. Example format:
```
source: planning/planner.py :: write_plan
dest:   planning/planner.py
reason: 3 of 3 callers live in destination module
```
or:
```
reason: same community (comm_2), 5 shared edges
```

This makes wrong destinations obvious at a glance and correct ones self-confirming. The model only needs to read the reason, not re-derive it.

---

### 3. `get_cluster_context` — Clear vs. Contested Split

**Problem:** `get_cluster_context` returns cohesion scores, edge counts, and community membership as raw evidence. The model then had to synthesize "what belongs together" and "what needs a placement decision" from that data — mechanical work that shouldn't require LLM reasoning.

**Fix:** Restructure the output as a ranked list in two sections:

**Clear moves** — files where ≥80% of cross-file edges stay within the cluster. These should move together. Surface them as a flat list with destination recommendation and confidence.
```
CLEAR (move together → planning/)
  planning/planner.py       — 9/10 edges internal
  planning/models.py        — 8/9 edges internal
```

**Contested** — files with mixed signals (edges split across clusters, bridge nodes). Surface these separately with the competing destinations shown explicitly.
```
CONTESTED (needs decision)
  layout.py  — 4 edges → planning/, 3 edges → execution/, 2 edges → interface/
```

The model only needs to reason over the contested section. Clear moves can be approved without further analysis.

---

### 4. Community Aliasing

**Problem:** Community IDs (`comm_0`, `comm_1`) shift between runs because Louvain community detection is non-deterministic. This is expected and not fixable at the algorithm level. However, it broke continuity across sessions — the model couldn't easily refer back to a prior community by ID.

**Fix:** Alias each community by its dominant directory or its highest-edge-count file, displayed alongside the numeric ID. Example:
```
comm_2  [alias: execution/]   — 6 files, cohesion 0.04
comm_5  [alias: planning/planner.py]  — 1 file, cohesion 0.08
```

The alias is derived mechanically from the community's files and changes predictably when community membership changes. It gives the model a human-readable handle without implying false stability.

---

## Decisions & Positions

- **Circular import check is a hard block in `approve_symbol_moves`**, not a warning and not a separate tool.
- **Hub files (god nodes) are excluded as move destinations** by the planner, not just flagged.
- **Proposal rationale is required** for every symbol move proposal — not optional metadata.
- **`get_cluster_context` output is structured**, not a raw evidence dump. Clear/contested split is the primary output shape.
- **Community IDs will continue to shift** between runs; aliasing is the mitigation, not stable IDs.
- **Sandbox loop and staged workflow are unchanged.**

---

## Open Questions

- **Carry-over cycle detection depth:** How many hops of transitive import checking is feasible before it becomes too slow for interactive use? One hop (direct cycle) is trivial; full transitive closure over a large codebase may not be.
- **Hub-file threshold:** What edge-count percentile marks a "hub file"? The existing god-node list (top 8 by edges) is a reasonable starting point but may need tuning per-repo.
- **Rationale generation cost:** Generating per-proposal rationale requires querying the graph for each symbol. For large symbol move lists this may be slow — worth benchmarking before shipping.

---

## Constraints & Boundaries

- These are **signal improvements within the existing tool**, not a redesign of the workflow.
- The LLM remains responsible for architectural judgment on contested moves. The tool's job is to filter the obvious cases, not to make all decisions.
- No new MCP tools should be added for these improvements — the surface area stays the same.
