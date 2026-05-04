# codescaffold

Graph-informed structural refactoring control plane for Python codebases.

`codescaffold` helps coding agents and humans reorganize Python projects safely by combining repository graph analysis, explicit refactor plans, mechanical rope/LibCST rewrites, sandboxed git worktrees, staged validation, and optional import-linter contracts.

It is not a fully autonomous architecture fixer. It is a control plane that makes large refactors inspectable, reviewable, and mechanically safer.

## Core idea

Coding agents are good at judgment-heavy work:

- deciding names
- interpreting intent
- explaining architecture
- writing documentation
- resolving ambiguous design choices

They are weaker at structural bookkeeping:

- remembering every import edge
- tracking file moves across a repo
- updating references consistently
- preserving package importability
- enforcing architecture after a refactor
- safely rolling back failed changes

`codescaffold` is designed to cover that mechanical side.

```text
graphify / graph data
    ↓
codescaffold analysis
    ↓
reviewable placement decisions
    ↓
approved file/symbol moves
    ↓
rope / LibCST rewrites
    ↓
sandbox validation
    ↓
contracts / audit trail
````

## Status

Experimental but functional.

The current project focus is:

* MCP-first workflow
* sandboxed structural refactoring
* graph-derived placement evidence
* staged validation
* import-linter contract generation
* agent-assisted rename/docstring workflow
* better use of graphify graph data beyond simple community-to-file grouping

## What it does

`codescaffold` can:

* analyze a Python repository graph
* identify communities/clusters in the codebase
* surface placement decisions for an agent to review
* show graph evidence for clusters and symbols
* approve selected file moves
* apply approved moves in a git worktree sandbox
* create required package `__init__.py` files
* rewrite imports after moves
* validate structurally and behaviorally
* apply package/module/symbol rename maps
* insert or replace docstrings
* generate import-linter contracts
* validate contracts
* merge or discard sandbox branches

## What it is not

`codescaffold` does not try to infer perfect architecture by itself.

It should not be used as:

* a blind auto-refactor button
* a replacement for code review
* proof that graph communities are architecturally correct
* proof that similar-looking functions are semantically equivalent
* a substitute for human/API compatibility judgment

The graph is evidence, not authority.

## Installation

Requires Python 3.11+.

```bash
git clone https://github.com/Xopher00/codescaffold.git
cd codescaffold
pip install -e ".[dev]"
```

The package installs the MCP server entry point:

```bash
codescaffold-mcp
```

## MCP usage

Register `codescaffold-mcp` with your MCP-capable coding agent.

Example MCP server configuration:

```json
{
  "mcpServers": {
    "codescaffold": {
      "command": "codescaffold-mcp",
      "args": []
    }
  }
}
```

Exact configuration depends on the agent or client you use.

## Current MCP tools

```text
analyze
validate
rollback
approve_moves
apply
get_cluster_context
apply_rename_map
rename
merge_sandbox
discard_sandbox
reset
get_symbol_context
insert_docstring
contracts
validate_contracts
```

### Tool roles

| Tool                  | Purpose                                                            |
| --------------------- | ------------------------------------------------------------------ |
| `analyze`             | Build or refresh graph-derived refactor plan artifacts             |
| `get_cluster_context` | Show graph evidence for a cluster/community                        |
| `approve_moves`       | Mark selected proposed moves as approved                           |
| `apply`               | Apply approved moves in a sandboxed worktree                       |
| `apply_rename_map`    | Rename placeholder packages/modules/symbols after structural moves |
| `rename`              | Perform an ad-hoc rope-backed rename                               |
| `get_symbol_context`  | Show graph context for a symbol                                    |
| `insert_docstring`    | Insert or replace a symbol docstring                               |
| `contracts`           | Generate or refresh import-linter contracts                        |
| `validate_contracts`  | Run import-linter against generated contracts                      |
| `validate`            | Run staged validation                                              |
| `rollback`            | Roll back recent applied changes where supported                   |
| `merge_sandbox`       | Merge a completed sandbox branch                                   |
| `discard_sandbox`     | Discard a sandbox branch                                           |
| `reset`               | Clear stale generated state/artifacts                              |

## Typical workflow

```text
1. analyze repository
2. inspect cluster context
3. approve selected moves
4. apply approved moves in sandbox
5. inspect result
6. apply rename map if placeholder names remain
7. validate
8. generate / validate contracts
9. merge or discard sandbox
```

A typical agent-guided flow:

```text
analyze
→ get_cluster_context
→ approve_moves
→ apply
→ apply_rename_map
→ validate
→ contracts
→ validate_contracts
→ merge_sandbox
```

The agent should make placement and naming decisions. `codescaffold` should perform the mechanical work.

## Sandboxed apply model

Destructive operations default to sandbox mode.

The sandbox mechanism uses git worktrees under paths like:

```text
/tmp/codescaffold_<timestamp>
```

The intended behavior is:

```text
create worktree branch
→ apply approved changes
→ validate
→ commit branch on success
→ keep branch for review/merge
→ discard on failure if requested
```

This makes large refactors auditable and reversible.

## Staged validation

Validation is split into phases.

Structural validation:

```text
compileall
syntax/import-shape checks where safe
```

Installability validation:

```text
import smoke checks
package import checks
entry point import checks where applicable
```

Behavioral validation:

```text
pytest or project test suite, when present
```

A project does not need to have tests for `codescaffold` to perform useful structural validation. However, existing human-written tests remain the strongest behavioral signal.

Generated tests, if added later, should be treated as smoke or characterization scaffolding, not as proof of correctness.

## Graphify integration

`codescaffold` uses graph-derived structure as the perception layer.

Graph evidence may include:

* files
* symbols
* communities
* imports
* calls
* source locations
* relation types
* edge confidence
* god/high-degree nodes
* bridge nodes
* cross-cluster edges
* surprising connections
* shortest paths

The project should not reduce graph data to only:

```text
community_id → files
```

The goal is to expose graph evidence in a way that helps agents make better placement and naming decisions.

## Placement review principles

Current directory layout is history, not proof of correct architecture.

A cluster should not be considered correct merely because its files are already co-located.

Placement decisions should consider:

* internal cohesion
* dependency direction
* incoming vs outgoing edges
* relation types
* bridge files
* god nodes
* cross-cluster coupling
* surprising connections
* import cycles
* edge confidence

Useful distinction:

```text
[co-located] means files are currently together.
It does not mean they are correctly placed.
```

## Import-linter contracts

`codescaffold` can generate import-linter contracts from graph-derived structure.

Supported contract concepts include:

* forbidden imports
* layers
* independence

Contracts are intended to turn discovered structure into enforceable architecture.

Open design question:

```text
Should generated contracts be temporary sandbox artifacts,
or durable architecture guards that survive merge?
```

For long-term use, contracts should likely be durable, refreshable, and validated after moves/renames.

## Rename and docstring workflow

Initial structural moves may use neutral placeholder names such as:

```text
pkg_000
mod_000.py
```

This keeps structural placement separate from semantic naming.

After the structural move succeeds, an agent can inspect graph context and apply a rename map through `apply_rename_map`.

This allows the agent to handle judgment-heavy naming while `codescaffold` handles mechanical rename and import updates.

Docstrings follow the same pattern:

```text
agent writes or revises docstring
→ codescaffold inserts it mechanically
```

## Generated artifacts

`codescaffold` writes reviewable artifacts such as:

```text
.refactor_plan/
STRUCTURE_REPORT.md
refactor_plan.json
state.json
.importlinter
```

Exact artifacts may vary by workflow stage.

Generated artifacts should make the refactor auditable:

* what graph was used
* what moves were proposed
* what moves were approved
* what validation passed
* what contracts were generated
* what branch was produced
* what still needs review

## Design principles

1. **Graph evidence first**
   Use repository structure, not vibes, to identify candidate boundaries.

2. **Agent judgment where needed**
   Let the agent decide names, intent, and ambiguous placement.

3. **Mechanical changes through tools**
   Use rope and LibCST for deterministic edits.

4. **Sandbox before merge**
   Destructive changes should happen in a git worktree branch first.

5. **Validation in phases**
   Do not fuse file moves, import rewrites, package creation, and pytest into one opaque step.

6. **Contracts should not go stale**
   If contracts are generated, they need a lifecycle: generate, validate, refresh, preserve or explicitly discard.

7. **No hidden architecture assumptions**
   Source roots, test paths, and package layout should be detected from config where possible and made explicit where not.

## Development

Install dev dependencies:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run linting if configured:

```bash
ruff check .
```

Run the MCP server locally:

```bash
codescaffold-mcp
```

## Project layout

High-level package areas:

```text
src/refactor_plan/
    mcp_server.py        MCP server entry point
    interface/           graph/worktree/user-facing interface utilities
    planning/            refactor plan construction
    execution/           mechanical apply/import rewrite operations
    records/             move/apply state records
    validation/          compile/import/test validation
    contracts/           import-linter contract generation
    naming/              rename/docstring support
    reporting/           structure reports
```

## Roadmap

Near-term priorities:

* improve graph evidence shown in `get_cluster_context`
* make placement guidance less biased toward current layout
* preserve richer audit trails for sandbox merges
* clarify contract lifecycle
* improve merge summaries
* replace or update stale demo/example code
* expose graphify-style graph queries more directly
* add safer generated smoke probes for importability

Later possibilities:

* read-only duplicate-logic/equivalence reports
* canonical symbol ownership proposals
* richer symbol-level move planning
* import-cycle-aware placement suggestions
* contract staleness detection
* stronger generated architecture reports

## License

MIT.

```
::contentReference[oaicite:1]{index=1}
```

[1]: https://raw.githubusercontent.com/Xopher00/codescaffold/main/pyproject.toml "raw.githubusercontent.com"
