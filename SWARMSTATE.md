# Swarm State — codescaffold rebuild

The following is a summary from a Codex agent assessing the project status. Be careful to not misunderstand the context, we do not want to waste time arguing over whether or not something is in scope or not.
<codex-summary>
Review the current CodeScaffold repo at commit bbd076bfab2c45a1ff2253ac5b4944461e943fea.

Treat this as a stabilization/debugging task, not a redesign.

Goal:
Make the existing graph-driven refactoring workflow mechanically reliable without deleting or weakening intended capabilities.

Focus first on the core path/module model. Audit and clarify how the system represents:

- Graphify source file paths
- absolute paths
- repo-relative paths
- source-root-relative paths
- Python module names
- package directories
- Rope resource paths
- final destination module paths

These concepts must not be conflated.

Use Graphify output as the source of structural evidence, but do not collapse it too early into only file-community lists. Preserve enough graph information for later planning, naming, validation, and rationale.

Tasks:

1. Run the current tests and identify real failures.
2. Check that Graphify is declared or documented as a dependency/prerequisite.
3. Review `graph_bridge.py`, `cluster_view.py`, `planner.py`, and the applicator path.
4. Ensure planned file moves distinguish destination package directories from final destination module files.
5. Ensure destination package folders are created before Rope file moves if needed.
6. Verify import rewrites use correct old/new module names, not filesystem directories.
7. Verify manifest and rollback records use consistent path semantics.
8. Add focused regression tests around path conversion and file move planning.

Do not:
- remove Graphify integration
- remove file moves
- remove symbol moves
- remove import rewriting
- stub rollback or validation
- simplify by deleting capabilities
- introduce a new architecture before understanding the current one

Deliverable:
A concise assessment of what was wrong, what was fixed, and what remains risky.
</codex-summary>
Repeat: The previous paragraph was a Code agent assessment. Treat it like an external review report. Must be taken into consideration fully post rung-10, see also the Deferred section of this prompt.

The rest of this file contains precise instructions for recreating and reinitiationg the code swarm used in this project.

Last updated: 2026-05-02

## How to resume

1. Open `~/.claude/plans/jolly-munching-shannon.md` — Plan v12 (full architecture context)
2. Run `/swarm:code` and use defaults
3. Tell the team: "Resume codescaffold rebuild. Rung 9.5 is committed (bbd076b). We are in recursive refinement at rung 9.75. Team config is at `.refactor_plan_team_config.json`. Prior session transcript: `~/.claude/projects/-home-scanbot-codescaffold/70828fd3-f8fd-4613-9546-51eb1b210b34.jsonl`"

## Team

| Name | Color | Model | Role |
|---|---|---|---|
| team-lead | — | sonnet | Lead — sole code author |
| principal-engineer | blue | opus | Socratic facilitator |
| python-tooling | green | sonnet | Python tooling specialist |
| systems-architect | yellow | sonnet | Architectural reviewer |
| impl-researcher | purple | sonnet | Implementation depth |
| dx-lead | orange | sonnet | DX/usability reviewer |

Full prompts are in `.refactor_plan_team_config.json`.

## Rung progress

| Rung | Commit | Status |
|---|---|---|
| 9.0 | b15d4e6 | ✅ done — 6 modules, 29 tests |
| 9.25 | b768105 | ✅ done — layout detect, shlex.split, analyze dry-run |
| 9.5 | bbd076b | ✅ done — stale cache, single-file fix, source root filter, validator exhaustive, import rewrite escalations |
| 9.75 | — | 🔄 probe sent, awaiting CONFIDENCE REACHED (Project interrupted at this point by context limit, may need to send a new probe) |
| 10 | — | pending |

## Test count
43 tests passing as of rung 9.5

## Deferred (post-rung-10)
- Edge provenance/confidence filtering in planner (graphify-API-dependent)
- grimp integration (SWARMRESUME scope — Wave F)
- Ruff/pycln/autoflake integration (SWARMRESUME scope)
- planner→applicator symbol-move end-to-end test
- `build_file_refs` src/ hardcode in graph_bridge.py

## Files changed across rungs
- `src/refactor_plan/applicator/` — apply.py, cleanup.py, file_moves.py, import_rewrites.py, manifests.py, models.py, rollback.py, symbol_moves.py
- `src/refactor_plan/interface/` — cli.py, cluster_view.py, graph_bridge.py
- `src/refactor_plan/planning/` — planner.py
- `src/refactor_plan/validation/` — validator.py
- `src/refactor_plan/naming/` — namer.py
- `src/refactor_plan/contracts/` — import_contracts.py
- `tests/` — conftest.py, test_applicator.py, test_planner_bridge.py
