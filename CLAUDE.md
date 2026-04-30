````markdown
# CLAUDE.md

## Project Identity

This project is a **graph-driven structural refactoring assistant**.

The goal is to make large codebase reorganization safer by separating:

1. **Structural analysis**
2. **Mechanical movement**
3. **Semantic naming**
4. **Behavioral refactoring**

Most refactoring tools and AI agents blur these together. This project must not.

Core rule:

> **Structure first. Names second. Semantics last.**

This is not a generic “AI refactor my code” tool. It is a staged system for discovering code structure, reorganizing it mechanically, validating it, and only then using an LLM for naming.

---

## Motivation

Refactoring large codebases is painful, slow, and risky.

AI coding agents often make it worse because they try to infer intent, rename concepts, move files, change abstractions, and edit behavior all at once. That creates fragile, hard-to-review changes.

This project exists because code structure can often be improved before semantic decisions are made.

The system should first ask:

- What code is structurally related?
- What files/classes/functions form natural clusters?
- Which modules are god modules?
- Which files are bridges between unrelated clusters?
- Which parts are misplaced?
- Which dependencies prevent clean architecture?

Only after the structure is mechanically coherent should an LLM help name things.

---

## Core Workflow

The intended workflow is:

```text
graph extraction
→ graph normalization
→ structural clustering
→ neutral relocation plan
→ mechanical application
→ validation
→ LLM semantic naming
→ mechanical rename application
→ validation again
````

Do not collapse these phases.

---

## Design Principles

### 1. Structure first

Use code graphs to determine what belongs together.

Prefer deterministic structural signals:

* containment
* imports
* calls
* inheritance
* shared dependencies
* shared callers
* source locations
* graph communities

Do not start by asking an LLM to “clean up the architecture.”

### 2. Neutral names before semantic names

Initial reorganization should use placeholder names such as:

```text
pkg_001
pkg_002
mod_001.py
cluster_001
component_001
```

This is intentional.

The placeholder phase separates:

```text
What belongs together?
```

from:

```text
What should this be called?
```

### 3. Mechanical edits before semantic edits

The first applied changes should be limited to safe mechanical operations:

* move files
* move modules
* update imports
* create compatibility shims
* generate manifests
* validate imports

Avoid behavior changes.

### 4. LLM naming happens after validation

The LLM should only name structures that already exist and pass validation.

The LLM may receive:

* files in a cluster
* classes/functions in the cluster
* callers/callees
* imports
* docstrings/comments
* neighboring clusters
* graph relationships

The LLM should return a rename map, not a new architecture.

### 5. Validation is mandatory

Every applied batch must be validated.

Prefer incremental, reversible changes over huge rewrites.

---

## Non-Goals

Do not build a vague autonomous refactoring agent.

Do not start with:

* function body rewrites
* function signature changes
* semantic abstraction merging
* deleting code
* collapsing layers
* renaming public APIs
* changing runtime behavior
* inferring semantic equivalence from graph structure alone

Graph clustering is evidence of structural relation. It is not proof of semantic equivalence.

---

## Known Tooling

The project should combine existing tools where practical.

### Graph extraction

Useful candidates:

* **Graphify**

  * Rich AST/code relationship graph extraction.
  * Can expose files, classes, functions, imports, calls, inheritance/use relationships, comments/docstrings, and source locations.
  * Useful for symbol-level clustering, not just module-level imports.

* **grimp / pydeps**

  * Useful for Python import graphs.
  * Simpler than Graphify.
  * Good for module/package-level architecture analysis.

### Graph processing

* **NetworkX**

  * Community detection
  * graph weighting
  * centrality
  * bridge node detection
  * cycle analysis
  * cluster analysis

### Mechanical refactoring

* **rope**

  * Python rename/move refactorings.
  * Useful for import-aware module and symbol movement.

* **LibCST**

  * Syntax-preserving Python codemods.
  * Useful when formatting/comments must be preserved.

* **Bowler**

  * Python codemod/refactoring support.

* **OpenRewrite**

  * Strong large-scale recipe-based refactoring model, especially relevant outside Python/JVM-heavy ecosystems.

### Architecture enforcement

* **Import Linter**

  * Enforce import boundaries after reorganization.

---

## Intended Architecture

### `extractor/`

Responsible for extracting or loading graph data.

Possible responsibilities:

* run Graphify
* consume Graphify `graph.json`
* optionally consume grimp/pydeps output
* normalize extractor-specific formats into the project graph model

This layer should not decide refactors.

---

### `graph_model/`

Defines the internal graph schema.

Preserve at least:

* node id
* node type
* symbol name
* source file
* source location
* edge source
* edge target
* relation type
* confidence, if available
* extractor provenance

The internal graph model should allow multiple extractors to feed the same planner.

---

### `clustering/`

Responsible for structural clustering.

Use weighted graph relationships.

Suggested initial weights:

```text
contains      = very strong
inherits      = strong
direct calls  = strong / medium
imports       = medium
uses          = medium
shared caller = medium
shared callee = medium
doc/comment similarity = weak/contextual
ambiguous/inferred edge = lower confidence
```

This layer should identify:

* natural clusters
* god modules
* bridge nodes
* isolated nodes
* cyclic regions
* likely misplaced files/symbols
* high-coupling regions

This layer should not edit code.

---

### `planner/`

Responsible for turning clusters into a relocation plan.

The planner should emit a dry-run plan first.

Output should include:

* proposed clusters
* proposed placeholder packages/modules
* files or symbols to move
* rationale for each move
* risk level
* dependencies affected
* whether compatibility shims are recommended

The main artifact should be something like:

```text
refactor_plan.json
```

The planner should start with file/module-level moves.

Symbol-level extraction is a later, riskier phase.

---

### `applicator/`

Responsible for applying approved plans.

Use mechanical tools such as rope, LibCST, or explicit file moves.

Responsibilities:

* move files/modules
* update imports
* create `__init__.py` files
* create compatibility shims
* preserve public import paths where useful
* record every change in a manifest

Avoid raw string replacement unless no safer option exists.

---

### `validator/`

Responsible for checking each batch.

Possible validation commands:

```bash
python -m compileall src tests
pytest
ruff check .
mypy .
pyright
```

The validator should also support import checks.

If a batch fails validation, the system should stop and report the failure.

---

### `namer/`

Responsible for LLM-assisted naming after structural validation.

The namer receives stable cluster context and returns a rename map.

It should not decide structural moves by default.

Example output:

```json
{
  "pkg_001": "backend",
  "mod_001.py": "compiler.py",
  "component_003": "EquationLowerer"
}
```

The rename map should be applied mechanically and validated again.

---

### `reporter/`

Responsible for human-readable output.

Reports should explain:

* original structure
* detected clusters
* proposed moves
* reasons for moves
* risk levels
* validation results
* final rename maps
* compatibility shims created
* unresolved risks

A useful dry-run report is valuable even before the tool can edit code.

---

## Required Artifacts

The system should produce explicit machine-readable artifacts.

Suggested artifacts:

```text
graph.normalized.json
clusters.json
refactor_plan.json
refactor_manifest.json
rename_map.json
validation_report.json
STRUCTURE_REPORT.md
```

The manifest should preserve:

* original path/name
* temporary placeholder path/name
* final semantic path/name, if available
* reason for movement
* validation status
* rollback information where possible

---

## MVP Scope

The first prototype should be conservative.

MVP should:

1. Accept a Python repository path.
2. Run or consume Graphify output.
3. Normalize the graph into an internal model.
4. Cluster at file/module level.
5. Produce neutral package/module relocation proposals.
6. Emit:

   * `refactor_plan.json`
   * `STRUCTURE_REPORT.md`
7. Optionally apply safe file/module moves.
8. Rewrite imports mechanically where possible.
9. Run validation commands if configured.

MVP should avoid:

* function body rewrites
* function signature changes
* public API renames
* symbol-level extraction
* deleting code
* behavior changes
* semantic abstraction merging

---

## Later Phases

After the MVP works:

1. Add stronger Graphify integration.
2. Add symbol-level clustering.
3. Add god-module splitting.
4. Add compatibility shim generation.
5. Add Import Linter contract generation.
6. Add LLM-assisted naming.
7. Add mechanical semantic rename application.
8. Add rollback support.
9. Add richer architecture reports.

---

## Agent Behavior Rules

When working on this project:

* Do not turn it into a generic AI refactoring agent.
* Do not skip the neutral placeholder phase.
* Do not use the LLM as the primary source of structure.
* Do not perform semantic rewrites during structural planning.
* Prefer dry-run planning before editing.
* Prefer reversible, incremental changes.
* Prefer existing refactoring/codemod libraries over raw text replacement.
* Validate after every applied batch.
* Keep artifacts explicit and inspectable.
* Make the tool useful even when it only reports and does not edit code.

The distinctive idea is:

```text
graph-driven structural discovery
+ neutral mechanical reorganization
+ validation
+ LLM naming only after structure is stable
```

```
```
