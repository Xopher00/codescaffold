# CLAUDE.md

## Project

Codescaffold is an MCP server for deterministic, mechanically safe Python refactoring.

It uses Graphify analysis as refactor evidence, creates persisted refactor plans, applies approved changes in git worktree sandboxes, validates results, and moves architectural boundaries toward enforceable import-linter contracts.

The agent decides. Codescaffold executes safely.

## Core Rules

- No project mutation outside a sandbox.
- No refactor execution without a persisted Codescaffold plan.
- The plan is the single source of truth.
- Direct operation tools update the plan; they do not mutate files directly.
- Graphify analysis is required before planning.
- Plans must record the graph snapshot/hash and must be rejected when stale.
- Codescaffold exposes curated refactor evidence, not raw graph dumps.
- MCP tools should be thin wrappers over application logic.
- Rope, LibCST, grimp, and import-linter are core infrastructure.
- Import-linter contracts are first-class architecture guardrails, not an afterthought.

## Authority

Read these before coding:

1. `CLAUDE.md`
2. `docs/CHECKPOINT.md`
3. `docs/NEXT_STEP.md`
4. `docs/ARCHITECTURE.md`
5. `docs/DECISIONS.md`

Authority order:

```text
CLAUDE.md              AI development rules
docs/                  durable design docs
docs/ARCHITECTURE.md   architecture boundaries
docs/DECISIONS.md      accepted decisions
docs/CHECKPOINT.md     current state / do-not-redo list
docs/NEXT_STEP.md      next bounded task only
notes/                 non-authoritative scratch
external/              read-only references
src/                   implementation
tests/                 behavioral evidence
````

## Intended Package Shape

Prefer the greenfield package name `codescaffold`.

```text
src/codescaffold/
  mcp/          thin MCP interface
  graphify/     Graphify integration and graph snapshots
  candidates/   graph-informed refactor candidates
  plans/        plan schema, lifecycle, approval, staleness
  operations/   typed mechanical refactor operations
  sandbox/      git worktree isolation
  validation/   tests, imports, lint, type, import-linter checks
  contracts/    import-linter and architecture contracts
  audit/        result summaries and durable records
```

Do not casually migrate old `src/refactor_plan/` code. Treat it as legacy reference unless a decision says otherwise.

## Workflow

Use this default workflow:

```text
refresh Graphify analysis
→ generate/inspect candidate
→ create one bounded plan
→ agent edits or approves plan
→ validate plan freshness
→ apply plan in sandbox
→ run validations
→ summarize audit result
→ merge only after explicit approval
```

Agents may recover from unusual situations, but must not skip plan persistence, sandboxing, or validation.

## Development Discipline

* Use a single-agent workflow.
* Do not perform broad scans unless needed.
* Do not implement major features during foundation work.
* Keep each task bounded and reviewable.
* Prefer schemas, contracts, and tests before behavior.
* Do not hide business logic in MCP tool handlers.
* Do not make broad “cleanup” edits.
* Do not treat Graphify clusters as architecture truth without review.
* Do not claim validation passed unless it was run.

## Validation

Use project-configured commands when available.

Common checks:

```bash
python -m pytest
python -m compileall src
ruff check .
ruff format .
mypy src
lint-imports
```

After meaningful changes, update:

* `docs/CHECKPOINT.md`
* `docs/NEXT_STEP.md`

```
```
