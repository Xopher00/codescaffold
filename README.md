<div align="center">

<h1>codescaffold</h1>

<p>Graph-driven structural refactoring assistant for Python codebases.</p>

</div>

---

## Description

`codescaffold` makes large-scale codebase reorganization safer and more reviewable by enforcing a strict separation of concerns: structural discovery first, mechanical movement second, semantic naming last. It extracts an AST relationship graph from your codebase using [graphify](https://github.com/graphify), detects natural code clusters via graph community algorithms, proposes neutral placeholder relocations, applies them mechanically with [rope](https://github.com/python-rope/rope), validates the result, and only then invokes a language model to suggest meaningful names. Every batch is validated and reversible.

## Features

- **Deterministic structure, not LLM guesswork** — clusters are derived from code graph signals (imports, calls, containment, inheritance, community membership), not language model inference
- **Staged pipeline** — analyze → apply → split → clean → name; each phase is a separate command with explicit artifacts
- **Neutral placeholder paths** — initial relocations use `pkg_NNN/mod_MMM.py` names so structural decisions and naming decisions stay independent
- **God-module splitter** — identifies low-cohesion files and bridge-node symbols, proposes targeted extractions into fresh modules
- **Dead-code eliminator** — surfaces symbols with no incoming edges and no `__all__` / entry-point presence, with human-gated deletion
- **Automatic validation and rollback** — every `--apply` path calls the configured validator and rolls back via rope history on failure
- **Compatibility shims** — preserves external import paths after module moves
- **Architecture contracts** — generates `import-linter` configuration from the cluster topology to enforce the new boundaries
- **Inspectable artifacts** — all intermediate results written to `.refactor_plan/` as JSON and Markdown before any code is changed

## Installation

**Requirements:** Python ≥ 3.11, [graphify](https://github.com/graphify) (must be installed and able to produce `graph.json`)

```bash
git clone https://github.com/Xopher00/codescaffold.git
cd codescaffold
pip install -e ".[dev]"
```

## Usage

All commands accept a repository path and write their artifacts to `<REPO>/.refactor_plan/`.

### 1. Analyze — build the structural plan

```bash
refactor-plan analyze path/to/repo
```

Loads `graphify-out/graph.json`, projects symbols to file-level clusters, produces `refactor_plan.json` and a human-readable `STRUCTURE_REPORT.md`. No code is changed.

### 2. Apply — execute file and symbol moves

```bash
refactor-plan apply path/to/repo
```

Applies the `file_moves` and `symbol_moves` from `refactor_plan.json` using rope. Runs the configured validator after each batch and rolls back on failure.

### 3. Split — break up god modules

```bash
# Dry run: produce split_plan.json
refactor-plan split path/to/repo

# Apply approved entries
refactor-plan split --apply path/to/repo
```

Detects low-cohesion clusters and bridge-node symbols. Edit `split_plan.json` to set `approved: true` on entries you want applied.

### 4. Clean — remove dead code

```bash
# Dry run: produce dead_code_report.json + DEAD_CODE_REPORT.md
refactor-plan clean path/to/repo

# Apply: requires both --apply and --confirmed, plus approved: true per entry
refactor-plan clean --apply --confirmed path/to/repo
```

Surfaces symbols with degree ≤ 1 and zero incoming `calls`/`imports_from`/`method` edges that are not exported via `__all__` or `[project.scripts]`. Edit the report JSON to set `approved: true` before applying.

### 5. Name — LLM semantic naming

```bash
# Dry run: produce rename_map.json
refactor-plan name path/to/repo

# Apply renames via rope
refactor-plan name --apply path/to/repo
```

Gathers cluster context and calls the Claude API to propose meaningful names for placeholder packages and modules. Requires `ANTHROPIC_API_KEY` to be set.

### Typical workflow

```bash
# Run graphify first to produce graph.json
graphify run path/to/repo

# Then work through the pipeline
refactor-plan analyze path/to/repo
# review STRUCTURE_REPORT.md and refactor_plan.json

refactor-plan apply path/to/repo
refactor-plan split path/to/repo        # review split_plan.json
refactor-plan split --apply path/to/repo
refactor-plan clean path/to/repo        # review dead_code_report.json
refactor-plan clean --apply --confirmed path/to/repo
refactor-plan name path/to/repo         # review rename_map.json
refactor-plan name --apply path/to/repo
```

## API Reference

The public Python API mirrors the CLI. All modules live in `refactor_plan`.

### `cluster_view.build_view(graph_json_path: Path) -> GraphView`

Loads a graphify `graph.json`, projects symbol-level communities to file clusters, and returns a `GraphView` containing `file_clusters`, `misplaced_symbols`, `god_nodes`, `surprising_connections`, `suggested_questions`, and `community_cohesion`.

### `planner.build_plan(view: GraphView, repo_root: Path) -> RefactorPlan`

Converts a `GraphView` into a `RefactorPlan` with `file_moves` (module-level relocations) and `symbol_moves` (cross-cluster extractions). All destination paths use `pkg_NNN/mod_MMM.py` placeholders.

### `applicator.rope_runner.apply_plan(plan: RefactorPlan, repo_root: Path) -> ApplyResult`

Applies the plan using rope `MoveModule` and `MoveGlobal` operations, rewrites cross-cluster imports, removes residue files, and injects `from __future__ import annotations` into all touched modules.

### `splitter.build_split_plan(view: GraphView, graph: nx.Graph, repo_root: Path) -> SplitPlan`

Identifies split candidates from `suggested_questions[low_cohesion | bridge_node]`, re-derives the affected node sets via `graphify.cluster.cohesion_score` and `nx.betweenness_centrality`, and allocates fresh `mod_NNN.py` destinations.

### `splitter.apply_split_plan(plan: SplitPlan, repo_root: Path, *, only_approved: bool = True) -> ApplyResult`

Applies approved symbol splits via sequenced rope `MoveGlobal` calls, runs the cross-cluster import rewriter, and validates.

### `cleaner.build_dead_code_report(view: GraphView, graph: nx.Graph, repo_root: Path) -> DeadCodeReport`

Derives isolated symbol candidates from `suggested_questions[isolated_nodes]`, intersects with zero-incoming-edge filter, excludes exports and entry-points, and writes `.refactor_plan/dead_code_report.json`.

### `cleaner.apply_dead_code_report(report: DeadCodeReport, repo_root: Path, *, confirmed: bool = False) -> ApplyResult`

Deletes approved dead symbols via libCST `RemovalSentinel.REMOVE` and runs `organize_imports` on each touched file. Raises `ValueError` if `confirmed=False`.

### `validator.validate(repo_root: Path, applied_count: int, ...) -> ValidationResult`

Runs the commands in `refactor.toml` and the `import-linter` contracts. Returns a `ValidationResult`; callers are expected to roll back on non-zero exit.

### `namer.propose_rename_map(view: GraphView, plan: RefactorPlan, repo_root: Path) -> RenameMap`

Gathers cluster context and calls the Claude API (requires `ANTHROPIC_API_KEY`) to produce a `RenameMap` from placeholder names to semantic names.

## License

MIT — see [LICENSE](LICENSE).
