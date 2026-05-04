You are resuming an interrupted refactor of CodeScaffold.

The previous session ran out of context mid-refactor. Assume the repository may be in an inconsistent transitional state.

Do NOT add features.
Do NOT redesign.
Do NOT start a new refactor.
Do NOT delete partially migrated files just because they look unused.
Do NOT disable capabilities.
Do NOT “clean up” aggressively.

Your job is to recover the intended current state and make the repository internally consistent.

First pass: read-only inventory.

Inspect the repo and produce:

1. Current package tree under src/refactor_plan
2. pyproject.toml entry points and dependencies
3. Missing modules referenced by imports or entry points
4. Files that appear half-migrated
5. Files that appear syntactically broken or newline-corrupted
6. Old modules still referenced after the refactor
7. New modules that exist but are not wired in
8. Tests that reference old paths
9. Commands that should exist according to README/pyproject/tests
10. The shortest path to make the package importable again

Do not edit files during this pass.

Then stop and report:
- what is missing
- what is inconsistent
- what likely happened during the interrupted refactor
- the exact minimal recovery plan

Recovery priorities:

1. Make source files syntactically valid.
2. Make package imports resolve.
3. Make CLI entry point resolve.
4. Make tests collect.
5. Make existing tests run.
6. Only then debug behavior.

When you are allowed to edit, make only minimal consistency fixes:
- restore or recreate missing module files if imports/entry points require them
- fix pyproject entry point if it points to the wrong module
- fix imports broken by the package split
- preserve the new intended module boundaries
- do not collapse everything back into one file
- do not continue feature development

After consistency recovery, run:

python -m compileall src
python -m pytest -q

Report exact files changed and why.

You are exploring how to build a single Python refactoring-suite engine for import/export analysis, cleanup, dependency graphing, and safe rewrite planning.

This is NOT a review of the current project codebase.

Do not start by auditing the repository’s architecture or proposing project-specific import changes. Instead, investigate how existing Python tooling can be composed into one coherent refactoring engine.

The following is a list of other tools that may be potentially underutilized. Do not treat this as a list of tools we need to use - some aspects of them may overlap with one another. Incorporating new tool uses must be determined by where present tool usage limitations appear.

<begin-tool-list>
Goal:
Design an engine that can analyze Python package imports, detect cleanup opportunities, understand package dependency structure, identify intentional public exports, propose normalized import paths, and perform safe rewrites with validation and rollback.

Tools to evaluate and integrate conceptually:

1. Ruff
   - Use for fast linting and import-related diagnostics.
   - Especially relevant rules:
     - F401: unused imports
     - I: import sorting / import organization
   - Evaluate how Ruff behaves in normal modules versus `__init__.py`.
   - Pay special attention to intentional re-exports using:
     - `from .module import Name as Name`
     - explicit `__all__`
   - Determine whether Ruff should be used as:
     - a diagnostic source,
     - a fixer,
     - or a validation pass after the engine performs rewrites.

2. pycln
   - Use for unused import cleanup.
   - Compare with Ruff’s unused-import handling.
   - Determine whether pycln adds value beyond Ruff, especially for cautious cleanup.
   - Evaluate whether it should run before or after export inference.

3. autoflake
   - Similar cleanup role to pycln.
   - Evaluate whether it is redundant if Ruff and/or pycln are used.
   - Determine if there are cases where autoflake is safer or more dangerous.

4. isort
   - Dedicated import sorting and grouping.
   - Compare against Ruff’s import sorting.
   - Determine whether the engine should rely on Ruff only, or optionally support isort for users who already have isort config.
   - Consider `pyproject.toml` compatibility and formatter conflicts.

5. pylint
   - Use as a slower, richer diagnostic source.
   - Evaluate import-related diagnostics such as cyclic imports, unused imports, broad imports, redefined imports, and import errors.
   - Determine whether pylint should be part of the core engine or an optional deep-validation mode.

6. pyright / mypy
   - Use for type-aware validation after import rewrites.
   - Do not treat these as primary refactoring tools.
   - Evaluate how they can detect broken exports, missing attributes, wrong import targets, and package boundary mistakes.
   - Consider whether both are needed or whether the engine should support whichever the project already uses.

7. grimp
   - Use as the main import graph engine.
   - Build package/module dependency graphs.
   - Identify:
     - package children,
     - internal dependencies,
     - external dependencies,
     - import cycles,
     - deep imports,
     - cross-layer imports,
     - modules that act as API surfaces.
   - Determine how Grimp data should be normalized into the engine’s own internal graph model.

8. import-linter
   - Built on Grimp.
   - Use for declarative architecture contracts.
   - Evaluate whether the engine should emit import-linter contracts from discovered structure or consume existing contracts as constraints.
   - Consider contracts such as:
     - forbidden imports,
     - layers,
     - independence,
     - package boundaries.
   - Determine whether import-linter belongs in:
     - discovery,
     - validation,
     - enforcement,
     - or all three.

9. pydeps
   - Use for visualization-oriented dependency graphing.
   - Compare against Grimp.
   - Determine whether pydeps is useful as an optional visualization backend rather than the core graph engine.

10. modulegraph
   - Evaluate whether it adds value for static dependency discovery.
   - Compare against Grimp and pydeps.
   - Determine whether it is useful mainly for packaging/runtime dependency discovery, or whether it should be excluded from the core engine.

11. LibCST
   - Use for safe, concrete-syntax-preserving rewrites.
   - This is likely the main rewrite layer.
   - Evaluate how it can:
     - rewrite import statements,
     - preserve formatting and comments,
     - modify `__init__.py`,
     - insert or update `__all__`,
     - replace deep imports with package-level imports,
     - apply changes file-by-file with a manifest.
   - Determine how rewrite plans should be represented before applying them.

12. Bowler
   - Built on fissix/lib2to3-style refactoring.
   - Compare against LibCST.
   - Determine whether it is still appropriate, or whether LibCST should be preferred for modern Python.
   - If Bowler is included, define a narrow reason for using it.

Core design question:
How can these tools be used together inside one refactoring-suite engine without making the engine brittle, redundant, or overly dependent on any one tool?

Explore an architecture like this, but do not treat it as mandatory:

1. Discovery phase
   - Read project configuration.
   - Detect source roots.
   - Detect package layout.
   - Discover `pyproject.toml`, Ruff config, isort config, mypy config, pyright config, pylint config.

2. Static import scan
   - Parse imports from Python files.
   - Use Grimp for dependency graph.
   - Optionally compare with pydeps/modulegraph if useful.
   - Produce a normalized internal graph:
     - modules,
     - packages,
     - symbols,
     - import edges,
     - re-export edges,
     - external dependencies,
     - cycles,
     - public API surfaces.

3. Export inference
   - Analyze `__init__.py` files.
   - Identify existing `__all__`.
   - Identify redundant alias re-exports.
   - Identify imported names that are likely public API.
   - Distinguish accidental unused imports from intentional exports.
   - Do not assume every import in `__init__.py` should be removed.
   - Define confidence levels for export inference.

4. Diagnostic aggregation
   - Run or model outputs from Ruff, pycln/autoflake, pylint, pyright/mypy.
   - Normalize diagnostics into a shared schema:
     - file,
     - line/column,
     - tool,
     - rule,
     - severity,
     - symbol/module involved,
     - suggested action,
     - confidence,
     - whether auto-fix is safe.

5. Planning phase
   - Build a refactoring plan rather than immediately changing files.
   - Proposed changes may include:
     - remove unused imports,
     - sort imports,
     - replace deep imports with package-level imports,
     - add missing re-exports to package `__init__.py`,
     - add or update `__all__`,
     - convert accidental imports into explicit aliases,
     - reject unsafe cleanup when export intent is ambiguous.
   - Plans should be reviewable and explainable.

6. Rewrite phase
   - Use LibCST as the preferred rewrite engine.
   - Preserve formatting and comments.
   - Apply small, reversible patches.
   - Write a manifest of all touched files and transformations.
   - Avoid lazy imports or runtime import tricks unless explicitly requested.

7. Validation phase
   - Re-run Ruff.
   - Re-run type checker if configured.
   - Re-run tests if available.
   - Rebuild Grimp graph and compare before/after.
   - Detect newly introduced cycles or broken imports.
   - Validate that public exports still resolve.

8. Rollback / safety
   - Every rewrite must be reversible.
   - The engine should support dry-run mode.
   - The engine should output:
     - summary,
     - diff,
     - graph changes,
     - unresolved ambiguities,
     - validation failures.

Important constraints:
- Do not blindly remove imports from `__init__.py`.
- Do not assume unused means unnecessary when the file is an API surface.
- Do not rely on text replacement for import rewrites.
- Do not use lazy loading as a solution to import cycles unless explicitly requested.
- Do not collapse public API boundaries without understanding export intent.
- Do not mix diagnostic responsibilities and rewrite responsibilities too tightly.
- Avoid building a giant custom linter if existing tools already provide reliable diagnostics.
- The engine should orchestrate tools, normalize their outputs, and make safe decisions.

Deliverables:
1. A proposed architecture for the refactoring-suite engine.
2. A clear table showing each tool’s role:
   - core dependency,
   - optional dependency,
   - diagnostic source,
   - rewrite backend,
   - validation backend,
   - visualization helper,
   - likely redundant/excluded.
3. A proposed internal data model for:
   - modules,
   - imports,
   - exports,
   - symbols,
   - diagnostics,
   - rewrite plans,
   - validation results.
4. A recommended execution pipeline.
5. A safety model for `__init__.py` and public re-exports.
6. A recommendation on which tools should actually be used in the first implementation.
7. A list of risks, especially around ambiguous exports, circular imports, namespace packages, dynamic imports, type-only imports, and generated files.

Keep the focus on designing the import/export refactoring engine itself, not on reviewing or changing the current project.
<end-tool-list>

The following is an example workflow for how one can use Graphify to efficiently explore a codebase and refactor it. Treat as a reference point and compare against current graphify usage.

<begin-graphify-workflow>
## Reference Workflow: Graphify-Driven Refactor Analysis

This workflow treats **Graphify** as the semantic/navigation layer in a larger refactoring engine. The goal is not to let Graphify make refactoring decisions directly, but to use its graph output to identify where more precise analysis should be applied.

### 1. Build the project graph

Run Graphify over the source tree to produce:

```text
GRAPH_REPORT.md
graph.json
communities / clusters
node and edge metadata
```

The report gives the first coarse map:

```text
core abstractions
high-degree nodes
community boundaries
surprising inferred relationships
thin or isolated nodes
possible missing documentation or edges
```

This is useful because refactoring is rarely only about imports or syntax. It is usually about **conceptual coupling**.

### 2. Use the report as a navigation index

Start from the report, not from the raw JSON.

Useful signals:

```text
God nodes
  likely architectural hubs or overloaded abstractions

Communities
  likely conceptual subsystems

Surprising connections
  hypotheses worth verifying

Knowledge gaps
  isolated or underconnected concepts

Inferred edges
  semantic clues, not facts
```

The report helps decide **where to inspect**, not **what to change**.

### 3. Query the graph for focused projections

Use Graphify’s query/path/explain commands to produce smaller views of the graph:

```bash
graphify query "Which modules participate in type construction?" --graph graph.json
graphify path "ConceptA" "ConceptB" --graph graph.json
graphify explain "ConceptName" --graph graph.json
```

This is the first form of projection:

```text
full graph
→ focused semantic neighborhood
```

The goal is to reduce a large graph into a smaller subgraph relevant to one question.

### 4. Treat Graphify edges by evidence level

Graphify output should be interpreted by provenance:

```text
EXTRACTED
  usually code-backed facts such as calls, definitions, imports, containment

INFERRED
  semantic hypotheses generated from context

AMBIGUOUS
  requires manual review
```

A refactor engine should not treat all edges equally. Inferred edges are excellent for discovery, but they should be verified with deterministic tools before edits are proposed.

### 5. Use Graphify to identify roles, not just dependencies

The main value is role discovery.

Instead of asking:

```text
Which files import the same package?
```

ask:

```text
Which components play the same role?
Which components are producer/consumer pairs?
Which components share a conceptual neighborhood?
Which abstraction bridges multiple communities?
```

For example, a graph may reveal that several files touch the same external substrate, but in different roles:

```text
type construction
term construction
schema registration
runtime evaluation
parser integration
validation
primitive registration
```

Those are not equivalent just because they use the same dependency.

### 6. Project Graphify data into role-specific overlays

The raw Graphify graph can be projected into narrower analytical views:

```text
type-substrate projection
term-construction projection
runtime projection
parser projection
validation projection
public-API projection
backend-dependency projection
```

Each projection asks a different question.

A useful projection shape is:

```text
source node
  → uses concept
  → role
  → source file
  → evidence
  → confidence/provenance
```

This lets the refactor engine classify relationships as:

```text
same specific role
related role
producer/consumer relationship
weak shared dependency
likely noise
```

### 7. Combine Graphify with deterministic evidence

Graphify is strongest as the semantic layer. A larger refactor engine should pair it with deterministic tools:

```text
Graphify
  semantic communities, hubs, conceptual relationships

AST / CST analysis
  exact imports, calls, annotations, inheritance, symbol usage

Import graph tools
  module dependency edges and package boundaries

Linters / type checkers
  validation after proposed changes
```

The workflow is:

```text
Graphify suggests where similarity or coupling may exist.
AST/CST analysis verifies the exact code pattern.
Import analysis checks architectural boundaries.
Validation tools confirm that changes preserve behavior.
```

### 8. Separate similarity from duplication

A key outcome of the workflow is avoiding false refactors.

Two components may be related because they share a conceptual role, but that does not mean their code should be merged.

Useful classifications:

```text
Duplicate pattern
  same role, same construction pattern, same API usage

Related role
  same subsystem, different responsibilities

Producer/consumer
  one defines an encoding or abstraction, another consumes it

Weak overlap
  shared generic symbols or broad dependency only
```

This distinction prevents a refactor engine from collapsing legitimate boundaries.

### 9. Use findings to generate constrained refactor candidates

The final output should be candidate-oriented, not command-oriented:

```text
Candidate:
  centralize repeated schema registration

Evidence:
  Graphify places both components in the same conceptual neighborhood.
  AST confirms both construct the same external type/schema objects.
  Import graph confirms the relationship crosses a boundary.

Confidence:
  high / medium / low

Suggested next validation:
  run tests
  rerun graph extraction
  compare before/after graph
```

Graphify contributes the **why this area matters** part. Deterministic analysis contributes the **what exactly changes** part.

## Summary

This workflow uses Graphify as a **semantic projection engine** inside a larger refactoring suite.

It is useful because it can surface:

```text
conceptual hubs
hidden coupling
role clusters
producer/consumer relationships
places where deterministic analysis should focus
```

It should not be treated as an automatic rewrite engine. Its best role is to guide refactoring analysis by turning a large codebase into smaller, role-specific graph projections that can then be verified with code-level evidence.
<end-graphify-workflow>

<begin-llm-role-exploration>
A lot of it can be automated mechanically. The LLM is most useful for **classification, interpretation, and proposing safe refactor candidates**, not for discovering basic facts.

## Mostly mechanical

These parts can be deterministic:

```text
Build graph
  run Graphify
  run import graph tools
  parse AST/CST

Extract evidence
  imports
  calls
  annotations
  inheritance
  symbol usage
  source locations
  edge provenance

Create projections
  filter by keyword / package / symbol / role
  collapse by file, module, community, package, class, function
  group by role labels
  rank similarity

Detect exact repetition
  same imported symbol
  same call pattern
  same constructor family
  same annotation pattern
  same dependency crossing

Validate
  run tests
  run type checker
  run linter
  rerun graph
  compare before/after
```

For example, these are fully automatable:

```text
“Which files call hydra.unification.unify_type_constraints?”
“Which modules construct TypeScheme?”
“Which modules import private modules across package boundaries?”
“Which modules use backend-only dependencies?”
“Which modules have the same exact role/family/pattern profile?”
```

An engine can do this without an LLM.

## Partly mechanical, partly judgment

These can be scored mechanically but usually need interpretation:

```text
Are these modules similar enough to compare?
Is this repeated pattern intentional?
Is this a producer/consumer relationship?
Is this a valid abstraction boundary?
Is this duplication or just parallel use of the same substrate?
Should the shared code live in module A, module B, or a new helper?
```

A deterministic engine can say:

```text
assembly.graph and algebra.sort both construct TypeScheme/TypeVariable.
```

But deciding:

```text
assembly.graph should delegate schema registration to a shared helper
```

requires architectural judgment.

The engine can propose it with confidence, but not know if it matches the intended design.

## Best use of an LLM

The LLM is strongest for turning evidence into a **refactor hypothesis**:

```text
Evidence:
  A and B share schema-registration patterns.
  A is an orchestrator.
  B owns domain type encoding.
  The repeated construction is low-level substrate plumbing.

Interpretation:
  B or a small helper should own the registration logic.
  A should call it rather than constructing substrate objects directly.
```

That kind of reasoning is hard to encode as static rules without making the engine brittle.

The LLM is also useful for:

```text
naming roles
summarizing graph neighborhoods
explaining why a candidate matters
separating “related” from “duplicate”
identifying producer/consumer relationships
writing reviewable refactor plans
generating prompts or patch instructions
```

## What should not rely on an LLM

Do not use an LLM as the source of truth for:

```text
whether an import exists
whether a symbol is used
whether a call happens
whether a test passed
whether a graph edge is extracted or inferred
whether a refactor changed dependencies
whether a file exports a name
```

Those should be deterministic.

The LLM should consume facts like:

```json
{
  "source": "module_a",
  "target": "hydra.core.TypeScheme",
  "usage": "call",
  "line": 195,
  "role": "schema-registration",
  "provenance": "AST_EXTRACTED"
}
```

not guess them.

## A good automation split

```text
Mechanical engine:
  collect facts
  build projections
  rank similarities
  detect exact patterns
  validate changes
  generate candidate evidence packets

LLM:
  interpret candidate evidence
  classify relationship type
  explain architectural significance
  suggest non-prescriptive refactor options
  identify risks and questions

Human:
  decide intent
  approve boundary changes
  choose whether a candidate becomes a refactor
```

## The practical sweet spot

A strong refactor engine could automate about **70–85% of the analysis pipeline**:

```text
graph build
projection
role tagging
similarity scoring
evidence extraction
candidate generation
validation
```

The remaining **15–30%** is where the LLM helps:

```text
architectural meaning
intent inference
abstraction naming
risk framing
prioritization
```

The key is to make the LLM operate on **small evidence bundles**, not the whole codebase.

## Ideal candidate object

A larger engine could produce objects like this:

```json
{
  "candidate": "schema-registration-consolidation",
  "relationship": "related-same-role",
  "confidence": 0.82,
  "modules": [
    "module_a",
    "module_b"
  ],
  "shared_patterns": [
    "constructs TypeScheme",
    "constructs TypeVariable",
    "registers schema type"
  ],
  "evidence": [
    {
      "file": "module_a.py",
      "line": 79,
      "code": "schema[n] = TypeScheme(...)"
    },
    {
      "file": "module_b.py",
      "line": 195,
      "code": "schema = {name: TypeScheme(...)}"
    }
  ],
  "mechanical_observation": "Both modules construct schema entries directly.",
  "llm_question": "Is one module an owner of this responsibility while the other is an orchestrator?"
}
```

That is the right interface between deterministic tooling and an LLM.

## Bottom line

Most of the discovery and scoring can be mechanical.

The LLM should not be needed to find the facts. It is needed to answer:

```text
What does this pattern mean?
Is this duplication, a producer/consumer boundary, or intentional parallel usage?
What is the safest refactor candidate?
How should this be explained to a developer?
```

That division keeps the system reliable while still getting useful architectural insight.

<end-llm-role-exploration>

Spawn a new swarm session and resume work, taking these facts and information into consideration.