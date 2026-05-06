````md
# Architecture

## Purpose

Codescaffold is an MCP server for deterministic, mechanically safe Python refactoring.

It uses Graphify analysis to provide refactor evidence, creates persisted refactor plans, applies approved changes in git worktree sandboxes, validates the result, and moves architecture toward enforceable import-linter contracts.

The agent decides what should change.

Codescaffold makes the change safe, mechanical, reviewable, and auditable.

## Core Principle

No mutation happens except by applying a persisted Codescaffold plan in a sandbox.

The plan is the execution contract.

## System Model

```text
Graphify analysis
→ curated refactor evidence
→ refactor candidate
→ persisted plan
→ agent approval/edit
→ sandbox apply
→ validation
→ audit summary
→ optional merge
````

## Primary Boundaries

### `codescaffold.mcp`

Thin MCP interface.

Responsibilities:

* expose tools to the coding agent
* validate tool inputs
* call application services
* return structured results and concise summaries

Must not contain:

* refactor logic
* Graphify interpretation logic
* sandbox logic
* validation orchestration
* business rules

### `codescaffold.graphify`

Graphify integration and graph snapshot handling.

Responsibilities:

* run or refresh Graphify analysis
* load `graph.json`
* understand Graphify node, edge, symbol, import, cluster, and semantic concepts
* compute graph snapshot identity
* provide curated evidence for refactor planning

Must not:

* expose raw graph dumps as the default MCP interface
* reimplement Graphify unnecessarily
* decide final architecture by itself

### `codescaffold.candidates`

Graph-informed refactor candidate generation.

Responsibilities:

* generate bounded refactor candidates from graph evidence
* attach curated evidence
* identify risk and confidence flags
* summarize why a candidate may be useful

Candidates are proposals, not commands.

### `codescaffold.plans`

Plan schema, lifecycle, approval, and freshness.

Responsibilities:

* create persisted plan files
* store graph snapshot/hash
* store repo snapshot when available
* store curated evidence
* store executable operations
* track status
* reject stale plans
* provide rebase/update paths

A plan represents one bounded refactor.

Expected lifecycle:

```text
draft
→ reviewed
→ approved
→ applied_to_sandbox
→ validated
→ ready_to_merge
→ closed
```

### `codescaffold.operations`

Typed mechanical refactor operations.

Responsibilities:

* model operations such as file move, symbol move, rename, import rewrite, export update
* translate approved plan operations into Rope/LibCST actions
* report structured failures

Operations must not execute independently of a plan.

Direct operation-style MCP tools may exist, but they must update the active plan rather than mutate files directly.

### `codescaffold.sandbox`

Git worktree isolation.

Responsibilities:

* create sandbox worktrees
* name branches/worktrees predictably
* enforce dirty-tree policy
* apply approved plans only inside sandboxes
* clean up discarded sandboxes
* support merge only after validation and approval

Main worktree mutation is forbidden.

### `codescaffold.validation`

Validation orchestration.

Responsibilities:

* run configured validation commands
* check imports
* run tests
* run lint/type checks where configured
* run import-linter checks
* report pass/fail results structurally

Validation should eventually include:

* compile checks
* package import checks
* unit tests
* MCP startup smoke test
* import-linter checks
* Graphify refresh
* before/after graph delta summary

### `codescaffold.contracts`

Architecture and import-linter contracts.

Responsibilities:

* generate proposed import-linter contracts
* check existing contracts
* represent intended boundaries
* distinguish proposed contracts from approved contracts

Contracts are first-class architecture artifacts.

They are not merely post-refactor documentation.

### `codescaffold.audit`

Durable result records.

Responsibilities:

* summarize what changed
* record plan ID
* record graph snapshot
* record sandbox ID
* record validation results
* classify diffs
* record generated contracts
* preserve evidence for later review

## External Tool Roles

### Graphify

Primary source of codebase graph evidence.

Codescaffold depends on Graphify for:

* files/modules
* symbols
* imports
* call relationships
* dependency relationships
* clusters/communities
* semantic summaries
* ownership or boundary hints
* strongly connected components
* centrality and hotspot signals

### Rope

Python-aware mechanical refactoring engine.

Used for:

* module moves
* symbol moves where supported
* renames
* project-wide reference updates

### LibCST

Syntax-preserving rewrite engine.

Used for:

* import rewrites
* codemods
* precise edits Rope cannot express safely
* formatting-preserving transformations

### grimp

Import graph inspection and validation.

Used for:

* cycle detection
* import relationship checks
* layer dependency analysis
* before/after import comparison

### import-linter

Architecture boundary enforcement.

Used for:

* proposed architecture contracts
* approved boundary contracts
* validation after moves
* regression prevention

## State and Artifacts

Codescaffold should maintain explicit, inspectable state.

Preferred direction:

```text
.codescaffold/
  state.json
  analyses/
  plans/
  sandboxes/
  audits/
  contracts/
```

A plan or audit record should make clear:

* which graph snapshot was used
* which candidate was selected
* which operations were approved
* which sandbox was created
* which files changed
* which validations ran
* which validations passed or failed
* which contracts were generated or checked

## Plan Requirements

Every executable plan must include:

* stable plan ID
* bounded goal
* target project root
* graph snapshot/hash
* base git commit SHA when available
* curated evidence
* executable operations
* risk flags
* confidence flags where useful
* validation requirements
* status

Plans should be machine-readable first.

Human-readable summaries may be generated from the canonical plan.

## Refactor Scope

Initial first-class operations:

* move file/module
* move symbol/function/class
* rename symbol
* rewrite imports
* update `__init__.py` exports when needed
* detect import cycles
* generate import-linter contracts
* validate import-linter contracts

Avoid semantic function/class body edits unless explicitly approved.

Avoid deletion in early versions unless deletion is explicitly modeled in the plan and validated.

## Architectural Invariants

* Graph evidence informs refactoring; it does not decide architecture alone.
* The agent approves or edits plans.
* Codescaffold executes plans mechanically.
* Every applied refactor happens in a sandbox.
* Plan freshness is checked before execution.
* Stale plans are refused.
* Import-linter contracts are first-class outputs.
* MCP handlers stay thin.
* Validation results must be explicit.
* Audit records must survive the session.

## Legacy Code Rule

Existing `src/refactor_plan/` code is legacy reference for the greenfield branch.

Do not migrate legacy modules casually.

Migration requires an accepted decision describing:

* what is being migrated
* why it is worth preserving
* where it belongs in the new architecture
* what coupling is being removed
* what tests prove the migration works

## Preferred Greenfield Package Shape

```text
src/codescaffold/
  mcp/
  graphify/
  candidates/
  plans/
  operations/
  sandbox/
  validation/
  contracts/
  audit/
```

## Initial Build Priority

Do not start with broad feature migration.

Start with the contract-first core:

1. Graph snapshot model
2. Refactor candidate model
3. Plan schema
4. Plan lifecycle
5. Operation model
6. Stale-plan rejection
7. Sandbox boundary model
8. Validation result model
9. Audit record model

Only after these are stable should MCP tools and legacy migration proceed.

```
```
