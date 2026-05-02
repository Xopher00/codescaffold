You are debugging and stabilizing CodeScaffold.

This is a debugging and behavior-preserving refactor task, not a redesign task. Preserve the existing refactoring engine and make it understandable, observable, tested, and correct.

Do not delete, disable, hide, stub, or downgrade existing capabilities. Specifically, do not remove or disable:

- analyze, apply, split, clean, or name
- graph-driven planning
- file/folder movement
- symbol movement
- import rewriting
- compatibility shims
- validation
- manifest/rollback behavior
- naming phase, where present

Do not replace real bugs with “unsupported,” mark commands experimental to avoid fixing them, remove inconvenient tests, rewrite the architecture, or change behavior merely to make tests pass.

The intended architecture is:

Graphify extracts structure.
CodeScaffold consumes Graphify output.
NetworkX performs lightweight graph analysis.
Planner emits neutral relocation plans.
rope and LibCST apply mechanical edits.
Validator checks results.
LLM naming happens only after structure is stable.

Your job is to restore this division of responsibility without deleting capabilities.

Before editing code, produce an integration map for each command:

- analyze
- apply
- split
- clean
- name

For each command, identify:

- entry point
- main functions/modules involved
- input and output artifacts
- whether Graphify CLI is called
- whether Graphify is imported as a library
- whether graphify-out/graph.json is consumed
- where NetworkX is used
- where rope is used
- where LibCST is used
- where filesystem paths become Python modules
- where Python modules become filesystem paths
- where Rope resources are created
- where filesystem mutations happen
- where imports are rewritten
- where manifests and rollback data are written
- what custom CodeScaffold logic duplicates Graphify, NetworkX, rope, or LibCST

Also audit pyproject.toml and confirm Graphify is declared correctly.

Then map applicator/rope_runner.py before refactoring. Identify these phases:

1. path/source resolution
2. LibCST preflight
3. file moves
4. module renames
5. future-annotation injection
6. symbol moves
7. organize imports
8. residue cleanup
9. cross-cluster import rewriting
10. manifest writing
11. rollback

Do not edit code until this map exists.

Core invariant:

Filesystem path, absolute path, repo-relative path, source root, package root, Python module name, package name, Rope resource path, and Graphify source_file are different concepts. They must be represented and converted explicitly.

Do not use fragile path assumptions such as Path(...).parts[-2], basename-only rglob fallbacks, or passing absolute filesystem paths directly into Rope resource APIs.

Required invariants:

- dry-run must not modify files
- apply must only modify files it reports
- every mutation must be recorded
- applied, skipped, failed, and blocked operations must be distinguishable
- reports must not claim success for failed/skipped/blocked operations
- rollback data must correspond to real changed files
- rollback must restore exactly the files changed
- file moves must update imports consistently
- folder moves must preserve package importability
- symbol moves must not corrupt source files
- methods must not be treated as top-level functions/classes unless explicitly supported
- compatibility shims must not create circular imports
- residue cleanup must never delete live code
- src-layout and flat-layout repos must both be handled intentionally
- __init__.py behavior must be explicit

Known failures to fix, in this order:

1. Planner KeyError

Failure:

    dest_cluster = community_to_pkg[m.target_community]

Root cause:

    misplaced_symbols can reference a target_community that is absent from file_clusters/community_to_pkg.

Fix:

    Validate every SymbolMove target before emitting it. If the target community has no file-backed cluster, do not crash. Record a blocked/escalated symbol move with diagnostics: symbol, source, target_community, available communities, and reason.

Add a focused regression test.

2. Rope None resource

Failure:

    file_move failed for assembly/lens.py:
    'NoneType' object has no attribute 'is_folder'

Root cause:

    Rope destination folder resolution returns None because filesystem paths and Rope resource paths are being confused.

Fix:

    Add explicit helpers for resolving Rope file/folder resources from repo-relative paths. Convert absolute paths to project-relative Rope paths before calling Rope. Raise clear custom errors before MoveModule.get_changes can receive None.

Add regression tests for flat layout, src layout, and nested packages.

3. CrossClusterImportRewriter path crash

Failure:

    self.original_src_pkg = Path(src_rel).parts[-2]
    IndexError: tuple index out of range

Root cause:

    Import rewriter assumes every source file has a parent package.

Fix:

    Replace this with explicit path/module/package metadata. source_package must be optional. Root-level modules and package-less modules must not crash.

Add regression tests for root-level module, one-package module, and src-layout module.

Before fixing behavior, build a failure matrix. For each failure, record:

- command
- fixture/repo
- expected behavior
- actual behavior
- responsible file/function
- violated invariant
- minimal reproduction
- category: path resolution, import rewrite, symbol move, file/folder move, shim, manifest, rollback, validation, or Graphify integration

Add regression tests before changing behavior. Tests should assert invariants, not just surface output.

Required focused tests include:

- missing target community in planner
- root-level module path
- one-package module path
- src-layout module path
- Rope folder resource resolution
- file move with absolute imports
- file move with relative imports
- symbol move to existing destination
- two symbols moving to the same destination
- import rewrite after same-batch file movement
- compatibility shim points to the new location
- manifest records applied/skipped/failed/blocked honestly
- rollback restores exactly changed files

Fix order:

1. path/module/resource normalization
2. planner target validation
3. graph-to-plan correctness
4. file/folder movement
5. import rewriting
6. compatibility shims
7. symbol movement
8. residue cleanup
9. manifest/rollback
10. command/report accuracy

Do not debug symbol movement until path/resource handling and file movement are verified. Do not debug cleanup until movement and import rewriting are verified.

Refactor rope_runner.py only after mapping it. Extract responsibilities one module at a time without changing behavior unless a regression test proves the current behavior is wrong.

Target modules:

- path_model.py
- rope_resources.py
- file_moves.py
- symbol_moves.py
- import_rewrites.py
- shims.py
- cleanup.py
- manifests.py
- rollback.py
- graphify_runner.py, if needed, only for locating/running Graphify and returning graph.json

Do not rewrite algorithms from scratch. Use existing tools where appropriate:

- Graphify for structural extraction
- NetworkX for graph operations
- rope for mechanical Python refactoring where reliable
- LibCST for syntax-preserving rewrites
- pathlib/importlib for path mechanics

Make dangerous operations observable with controlled debug logging or structured report/manifest fields. For each move, record:

- operation id
- source path
- destination path
- source module
- destination module
- package metadata
- Rope resource path
- Graphify source_file
- symbols moved
- files touched
- imports rewritten
- shims created
- cleanup actions
- validation result
- manifest entry
- rollback entry
- failure classification

If an operation cannot be safely completed, do not silently skip it and do not delete the feature. Classify it, record why it failed, leave the repo safe, preserve it in the report as failed or blocked, and add a regression test if it exposes a bug.

Work in small patches. Good patches have one purpose, such as:

- fix module-name conversion for src-layout repos
- add safe Rope folder resource resolver
- make source_package optional in import rewriter
- record blocked symbol moves honestly
- prevent residue cleanup from deleting unrelated code

Bad patches:

- clean up applicator
- simplify refactor engine
- rewrite movement system
- disable unstable commands

Run focused tests first, then the full suite. Report exact commands run and results.

Final deliverable must include:

- integration map for analyze/apply/split/clean/name
- rope_runner.py execution map
- ownership problems found
- invariants documented
- failure matrix
- bugs fixed
- tests added/updated
- modules extracted
- remaining known bugs
- exact commands run
- test results
- files changed and why

The correct solution is not to amputate the refactoring engine.

The correct solution is to make CodeScaffold’s existing engine debuggable, tested, honest, and aligned with the intended architecture:

Graphify extracts.
NetworkX analyzes.
Planner plans.
rope/LibCST apply.
Validator checks.
LLM names later.