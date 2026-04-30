You are helping design and prototype a side project: a graph-driven structural refactoring assistant.

Core motivation:
Refactoring large codebases is painful, slow, and risky. Existing AI coding agents often make this worse because they jump directly from messy code to semantic rewrites: they rename things, move abstractions, alter structure, and infer intent all at once. That makes the process fragile. The goal of this project is to separate structural reorganization from semantic interpretation.

The central idea:
Use code graph extraction to discover the actual structure of a codebase, then use deterministic clustering and mechanical rewrite tools to reorganize code into a more coherent layout before asking an LLM to perform semantic naming.

This is not meant to be “LLM refactors everything magically.” The LLM should not be the first mover. The system should first build a structural map of the codebase, produce a neutral relocation plan, apply mechanical edits safely, validate the result, and only then ask an LLM to name the resulting clusters/modules/symbols.

High-level pipeline:

1. Extract a rich code graph
   - Use Graphify or a similar AST/code graph extractor.
   - Graphify is interesting because it can extract more than simple imports:
     - files
     - classes
     - functions
     - methods
     - imports
     - calls
     - inheritance/use relationships
     - docstrings/comments/rationale
     - source locations
     - graph edges such as contains/imports/calls/uses/inherits
   - A normal import graph is useful, but Graphify may be more powerful because it can expose symbol-level relationships, not just module-level dependencies.

2. Build a structural graph model
   - Load graph.json or equivalent output.
   - Normalize nodes and edges into an internal model.
   - Preserve metadata:
     - node id
     - node type
     - source file
     - line/range
     - symbol name
     - relation type
     - edge confidence if available
   - Treat the graph as the ground truth for structural analysis.

3. Cluster the graph
   - Use NetworkX or another graph library.
   - Run community detection / clustering.
   - Weight relationships differently:
     - contains = very strong
     - inheritance = strong
     - direct calls = strong/medium
     - imports = medium
     - shared callers/callees = medium
     - comments/docstring similarity = weak/contextual
     - ambiguous/inferred edges = lower confidence
   - The goal is to discover structurally coherent groups of files/classes/functions.

4. Produce a neutral relocation plan
   - Do not immediately invent semantic names.
   - Generate placeholder packages/modules such as:
     - pkg_001
     - pkg_002
     - mod_001.py
     - component_001
     - cluster_001
   - This neutral naming phase is critical.
   - It separates the question “what code belongs together?” from “what should this thing be called?”
   - The first refactor should prioritize structural coherence, not beautiful names.

5. Apply mechanical refactors
   - Use existing safe rewrite/refactoring tools rather than raw string replacement where possible.
   - Known relevant tools:
     - Graphify: rich AST/code relationship graph extraction
     - grimp / pydeps: Python import graph extraction
     - NetworkX: graph clustering and community detection
     - rope: Python rename/move/refactoring operations
     - LibCST: syntax-preserving codemods that preserve formatting/comments
     - Bowler: Python codemod/refactoring support
     - OpenRewrite: large-scale recipe-based refactoring, especially stronger in JVM ecosystems
     - Import Linter: enforce architecture/import boundaries after reorganization
   - The system should likely combine:
     - Graphify for rich structural extraction
     - NetworkX for clustering
     - rope / LibCST for mechanical edits
     - tests/typecheck/lint/import checks for validation
     - LLM only after mechanical structure is stable

6. Validate after every batch
   - Never apply a huge mass refactor without validation.
   - Run checks after each planned batch:
     - compile/import checks
     - unit tests
     - lint
     - type checker if configured
     - import graph validation
   - The tool should prefer incremental batches over one massive rewrite.

7. Use LLM for semantic naming only after structure is stable
   - Once pkg_001/pkg_002/etc. are mechanically valid and tests pass, use an LLM to suggest names.
   - The LLM should be given:
     - files in the cluster
     - contained classes/functions
     - callers/callees
     - docstrings/comments
     - neighboring clusters
     - import/call relationships
   - The LLM should return a rename map only.
   - It should not propose structural moves during the naming phase unless explicitly asked.
   - Example output:
     {
       "pkg_001": "backend",
       "mod_001.py": "compiler.py",
       "component_003": "EquationLowerer"
     }

8. Apply semantic rename map mechanically
   - Use rope/LibCST/import-aware tools.
   - Validate again.
   - Keep a manifest mapping:
     - original path/name
     - temporary placeholder path/name
     - final semantic path/name
     - reason for move
     - validation status

The intended architecture of the side project:

- extractor/
  - Runs Graphify or consumes Graphify output.
  - Optionally supports grimp/pydeps for simpler import-only mode.
  - Produces a normalized internal graph model.

- graph_model/
  - Defines node/edge schema.
  - Normalizes different extractor outputs.
  - Stores source locations and confidence metadata.

- clustering/
  - Applies edge weighting.
  - Runs community detection.
  - Produces clusters.
  - Identifies god modules, bridge nodes, isolated nodes, misplaced symbols, and cyclic regions.

- planner/
  - Converts clusters into a relocation plan.
  - Starts with module/package-level plans.
  - Later can support symbol-level extraction/splitting.
  - Emits refactor_plan.json.
  - Uses neutral placeholder names only.

- applicator/
  - Applies the plan.
  - Moves files/modules.
  - Rewrites imports.
  - Creates compatibility shims when needed.
  - Uses rope/LibCST instead of unsafe regex wherever possible.

- validator/
  - Runs compile checks, tests, linters, type checkers, and import checks.
  - Records pass/fail per batch.
  - Stops or rolls back on failure.

- namer/
  - Uses an LLM to name already-stabilized clusters.
  - Takes graph context as input.
  - Emits rename_map.json.
  - Does not decide structural moves by default.

- reporter/
  - Produces human-readable reports:
    - original structure
    - detected clusters
    - proposed moves
    - reasons
    - risk level
    - validation results
    - final rename map

Important design principles:

- Structure first, names second, semantics last.
- Do not ask the LLM to do everything at once.
- Do not treat graph clustering as proof of semantic equivalence.
- Use graph structure to plan reorganization, not to blindly rewrite behavior.
- Prefer file/module-level reorganization first.
- Symbol-level moves are a later, riskier phase.
- Keep every move reversible.
- Emit manifests and reports.
- Preserve compatibility shims where useful.
- Validate after every batch.
- Make the system useful even before it can edit code: a dry-run planner is already valuable.

MVP target:
Build a first prototype that does not yet perform dangerous symbol-level rewrites.

MVP should:

1. Accept a Python repo path.
2. Run or consume Graphify output.
3. Normalize graph.json into an internal graph.
4. Cluster the graph at file/module level.
5. Produce a proposed package/module reorganization using neutral names.
6. Emit:
   - refactor_plan.json
   - markdown report explaining clusters and move rationale
7. Optionally apply only safe file moves/import rewrites in a controlled batch.
8. Run validation commands if configured.

The first prototype should avoid:
- changing function bodies
- changing function signatures
- renaming public symbols
- inferring semantic equivalence
- deleting code
- collapsing abstractions
- “cleaning up” behavior

After MVP:
Add LLM-assisted naming as a second phase. The LLM should receive only stable cluster context and return a rename map. Then the system applies that rename map mechanically and validates again.

Please review this idea critically and help design the first implementation. Focus on:
- what the minimal viable architecture should be
- how to model the graph
- how to weight edges
- how to generate safe relocation plans
- where Graphify is useful versus where simpler tools like grimp/pydeps are enough
- how rope/LibCST should be used safely
- how to avoid unsafe semantic rewrites
- how to make the system incremental, reversible, and test-driven

Do not turn this into a vague AI refactoring agent. The distinctive idea is a staged, graph-driven, mechanically validated workflow:

graph extraction
→ structural clustering
→ neutral placeholder reorganization
→ validation
→ LLM semantic naming
→ mechanical rename application
→ validation again
