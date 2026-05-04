

from pathlib import Path
import json
import subprocess
from ._worktree import (
    commit_and_release, create_worktree, create_worktree_from_branch,
    discard_worktree, load_state, save_state, translate_plan,
)
from refactor_plan.server_helpers import (
    _build_trace, _cluster_for_file, _format_validation, _load_plan,
    _out_dir, _repo, _reset_stale_artifacts, _sandbox_result, _summarise_result,
)
from refactor_plan.execution import rename_module as do_rename_module, rename_symbol as do_rename_symbol, AppliedAction, ApplyResult, Escalation
from refactor_plan.execution.apply import _ensure_package_inits, _run_import_rewrites, apply_plan as do_apply_plan
from refactor_plan.execution.file_phase import _cleanup_empty_source_dirs, _run_file_moves
from refactor_plan.planning import write_plan, RefactorPlan
from refactor_plan.applicator import apply_symbol_move
from refactor_plan.records import write_manifest
from refactor_plan.contracts import generate_contracts as do_generate_contracts
from refactor_plan.naming import apply_rename_map as do_apply_rename_map, RenameEntry, RenameMap
from refactor_plan.validation import validate as do_validate
from refactor_plan import detect_layout


def apply(repo: str = "", sandbox: bool = True) -> str:
    """Apply the refactor plan (file moves + symbol moves + import rewrites).

    With sandbox=True (default), runs in a git worktree, validates, then commits
    to a branch for review.  Merge with:
        git merge <branch>
    Pass sandbox=False to apply directly to the working tree.
    """
    import rope.base.project as rp

    root = _repo(repo)
    plan = _load_plan(root)

    def _plan_dict(p: RefactorPlan) -> dict:
        return {
            "file_moves": [
                {"source": m.source, "dest": m.dest, "dest_package": m.dest_package}
                for m in p.file_moves
            ],
            "symbol_moves": [
                {"source": m.source, "dest": m.dest, "symbol": m.symbol}
                for m in p.symbol_moves
                if m.approved
            ],
            "source_root": p.source_root,
        }

    approved_symbol_moves = [m for m in plan.symbol_moves if m.approved]
    if not plan.file_moves and not approved_symbol_moves and plan.pending_decisions:
        return (
            "No moves approved yet.\n"
            "Run get_cluster_context to review graph evidence, "
            "then approve_moves with your placement decisions."
        )

    if not sandbox:
        result = do_apply_plan(_plan_dict(plan), root, _out_dir(root))
        return _summarise_result(result)

    wt_path, branch = create_worktree(root)
    try:
        wt_plan = translate_plan(plan, root, wt_path)
        wt_out = _out_dir(wt_path)
        wt_out.mkdir(parents=True, exist_ok=True)
        write_plan(wt_plan, wt_out / "refactor_plan.json")

        layout = detect_layout(wt_path)
        wt_src = str(layout.source_root)
        wt_env = {"PYTHONPATH": wt_src}

        # Pre-create all destination package dirs before rope runs.
        # Rope creates the directory on the first move; subsequent moves to the
        # same directory fail with EEXIST.  Creating them up-front is idempotent.
        pre_dest_dirs = {Path(m.dest_package) for m in wt_plan.file_moves}
        _ensure_package_inits(pre_dest_dirs, layout.source_root)

        # --- Phase 1: file moves ---
        project = rp.Project(str(wt_path))
        file_moves = [
            {"source": m.source, "dest": m.dest, "dest_package": m.dest_package}
            for m in wt_plan.file_moves
        ]
        try:
            applied, failed, dest_dirs = _run_file_moves(project, file_moves, dry_run=False)
        finally:
            project.close()

        result = ApplyResult(applied=applied, failed=failed)
        if result.failed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (file moves) — worktree discarded.\n" + _summarise_result(result)

        # --- Phase 2: __init__.py creation + empty source dir cleanup ---
        _ensure_package_inits(dest_dirs, layout.source_root)
        _cleanup_empty_source_dirs(result.applied, layout.source_root)

        # --- Structural validation: compileall after moves ---
        v1 = do_validate(wt_path, env=wt_env, mode="structural", layout=layout)
        if not v1.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (structural — after moves) — worktree discarded.\n" + _format_validation(v1)

        # --- Installability check: can we import the package after moves? ---
        v2 = do_validate(wt_path, env=wt_env, mode="installability", layout=layout)
        if not v2.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (installability — after moves) — worktree discarded.\n" + _format_validation(v2)
        wt_symbol_moves = [m for m in wt_plan.symbol_moves if m.approved]
        for sm in wt_symbol_moves:
            sym_result = apply_symbol_move(
                Path(sm.source), Path(sm.dest), sm.symbol, wt_path
            )
            if isinstance(sym_result, AppliedAction):
                result.applied.append(sym_result)
            else:
                result.failed.append(sym_result)

        if result.failed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (symbol moves) — worktree discarded.\n" + _summarise_result(result)

        # --- Phase 3: import rewrites ---
        src_root = layout.source_root if wt_plan.source_root else None
        _, skipped = _run_import_rewrites(result.applied, wt_path, src_root)
        result.skipped.extend(skipped)

        # --- Structural validation: compileall after import rewrites ---
        v3 = do_validate(wt_path, env=wt_env, mode="structural", layout=layout)
        if not v3.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (structural — after import rewrites) — worktree discarded.\n" + _format_validation(v3)

        # --- Behavioral validation: pytest ---
        v4 = do_validate(wt_path, env=wt_env, mode="behavioral", layout=layout)
        if not v4.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (behavioral/pytest) — worktree discarded.\n" + _format_validation(v4)

        wt_out.mkdir(parents=True, exist_ok=True)
        write_manifest(result, wt_out)

        files_moved = sum(1 for a in result.applied if a.kind.value == "FILE")
        symbols_moved = sum(1 for a in result.applied if a.kind.value == "SYMBOL")
        imports_rewritten = sum(a.imports_rewritten for a in result.applied)
        trace = _build_trace(
            root, "apply",
            files_moved=files_moved,
            symbols_moved=symbols_moved,
            imports_rewritten=imports_rewritten,
            validation="PASSED",
        )
        commit_and_release(root, wt_path, f"refactor: apply file moves\n\n{trace}")

        # Only gate on rename if moves used pkg_NNN placeholder directories.
        # When destinations are already semantic names, the branch is final.
        import re as _re
        _placeholder_pattern = _re.compile(r"(^|[\\/])pkg_\d+($|[\\/])")
        _needs_rename = any(
            _placeholder_pattern.search(m.dest_package)
            for m in wt_plan.file_moves
        )
        if _needs_rename:
            save_state(_out_dir(root), pending_rename_branch=branch)

        # Refresh contracts to reflect the new module layout
        try:
            _gp = ensure_graph(root)
            _view = build_view(_gp)
            do_generate_contracts(plan, _view, _gp, root, layout, force=False)
        except Exception:
            pass

        if _needs_rename:
            return (
                f"{_summarise_result(result)}\n\n"
                f"Structural moves committed to branch '{branch}'.\n"
                f"DO NOT MERGE YET — placeholder names are not final.\n"
                f"Next: get_cluster_context → apply_rename_map\n"
                f"Discard: git branch -D {branch}"
            )
        return (
            f"{_summarise_result(result)}\n\n"
            f"Validation PASSED. Changes committed to branch '{branch}'.\n"
            f"Review : git diff HEAD...{branch}\n"
            f"Apply  : git merge {branch}\n"
            f"Discard: git branch -D {branch}"
        )

    except Exception:
        discard_worktree(root, wt_path, branch)
        raise




def apply_rename_map(rename_map_json: str, repo: str = "", sandbox: bool = True) -> str:
    """Rename placeholder packages to semantic names.

    rename_map_json — JSON object mapping placeholder names to new names,
    e.g. '{"pkg_001": "auth", "pkg_002": "pipeline"}'.

    Uses rope to rename each package directory and rewrite all imports.
    With sandbox=True (default), changes are applied in a git worktree,
    validated, then committed to a branch for review.  Merge with:
        git merge <branch>
    Pass sandbox=False to apply directly to the working tree.
    """
    root = _repo(repo)
    try:
        mapping: dict[str, str] = json.loads(rename_map_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    rename_map = RenameMap(entries=[
        RenameEntry(old_name=k, new_name=v) for k, v in mapping.items()
    ])

    if not sandbox:
        plan = _load_plan(root)
        result = do_apply_rename_map(rename_map, plan, root, _out_dir(root), dry_run=False)
        return _summarise_result(result)

    # --- sandboxed path ---
    plan = _load_plan(root)
    state = load_state(_out_dir(root))
    base_branch = state.get("pending_rename_branch")

    if base_branch:
        wt_path, branch = create_worktree_from_branch(root, base_branch)
    else:
        wt_path, branch = create_worktree(root)

    try:
        wt_plan = translate_plan(plan, root, wt_path)
        wt_out = _out_dir(wt_path)
        wt_out.mkdir(parents=True, exist_ok=True)
        write_plan(wt_plan, wt_out / "refactor_plan.json")

        result = do_apply_rename_map(rename_map, wt_plan, wt_path, wt_out, dry_run=False)

        if result.failed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (apply errors) — worktree discarded.\n" + _summarise_result(result)

        wt_layout = detect_layout(wt_path)
        wt_env = {"PYTHONPATH": str(wt_layout.source_root)}
        validation = do_validate(wt_path, env=wt_env, layout=wt_layout)
        if not validation.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (validation) — worktree discarded.\n" + _format_validation(validation)

        renames_applied = [{"from": e.old_name, "to": e.new_name} for e in rename_map.entries]
        trace = _build_trace(
            root, "apply_rename_map",
            renames=renames_applied,
            imports_rewritten=sum(a.imports_rewritten for a in result.applied),
            validation="PASSED",
        )
        commit_and_release(root, wt_path, f"refactor: apply rename map\n\n{trace}")

        # Clear the pending rename state now that renames are committed.
        if base_branch:
            save_state(_out_dir(root), pending_rename_branch=None)

        # Refresh contracts after renames change module names
        try:
            graph_path = ensure_graph(root)
            view = build_view(graph_path)
            _plan = _load_plan(root)
            _layout = detect_layout(root)
            do_generate_contracts(_plan, view, graph_path, root, _layout, force=False)
        except Exception:
            pass

        return _sandbox_result(branch, _summarise_result(result))

    except Exception:
        discard_worktree(root, wt_path, branch)
        raise



# ---------------------------------------------------------------------------
# Ad-hoc rename
# ---------------------------------------------------------------------------


def rename(target: str, new_name: str, repo: str = "", sandbox: bool = True) -> str:
    """Rename a symbol, module, or package — propagates to all call sites.

    target formats:
      'src/pkg/mod.py::MyFunc'   — rename a function or class
      'src/pkg/mod.py'           — rename a module file
      'src/pkg/'                 — rename a package directory

    new_name is the simple identifier (no path, no extension).
    With sandbox=True (default), runs in a git worktree, validates, then
    commits to a branch.  Pass sandbox=False to apply directly.
    """
    root = _repo(repo)

    def _run_rename(base: Path) -> AppliedAction | Escalation:
        if "::" in target:
            file_part, symbol_name = target.split("::", 1)
            file_path = (base / file_part).resolve()
            return do_rename_symbol(base, file_path, symbol_name, new_name)
        module_path = (base / target).resolve()
        return do_rename_module(base, module_path, new_name)

    if not sandbox:
        action = _run_rename(root)
        if isinstance(action, Escalation):
            return f"FAILED: {action.reason}"
        return (
            f"Renamed '{target}' → '{new_name}'\n"
            f"  Strategy: {action.strategy.value if action.strategy else '?'}\n"
            f"  Files touched: {len(action.files_touched)}\n"
            f"  Imports rewritten: {action.imports_rewritten}"
        )

    # --- sandboxed path ---
    wt_path, branch = create_worktree(root)
    try:
        action = _run_rename(wt_path)

        if isinstance(action, Escalation):
            discard_worktree(root, wt_path, branch)
            return f"FAILED: {action.reason} — worktree discarded."

        wt_layout = detect_layout(wt_path)
        wt_env = {"PYTHONPATH": str(wt_layout.source_root)}
        validation = do_validate(wt_path, env=wt_env, layout=wt_layout)
        if not validation.passed:
            discard_worktree(root, wt_path, branch)
            return "FAILED (validation) — worktree discarded.\n" + _format_validation(validation)

        try:
            plan = _load_plan(root)
        except FileNotFoundError:
            plan = None
        trace = _build_trace(
            root, "rename",
            source=target,
            dest=str(Path(target).parent / f"{new_name}.py"),
            imports_rewritten=action.imports_rewritten,
            validation="PASSED",
            **_cluster_for_file(plan, target),
        )
        commit_and_release(root, wt_path, f"refactor: rename '{target}' → '{new_name}'\n\n{trace}")
        summary = (
            f"Renamed '{target}' → '{new_name}'\n"
            f"  Strategy: {action.strategy.value if action.strategy else '?'}\n"
            f"  Files touched: {len(action.files_touched)}\n"
            f"  Imports rewritten: {action.imports_rewritten}"
        )
        return _sandbox_result(branch, summary)

    except Exception:
        discard_worktree(root, wt_path, branch)
        raise



# ---------------------------------------------------------------------------
# Sandbox management
# ---------------------------------------------------------------------------


def merge_sandbox(branch: str, repo: str = "") -> str:
    """Merge a sandbox branch produced by apply_rename_map or rename.

    Runs git merge --no-ff so the refactor is recorded as a single merge commit.
    Safe to call only after reviewing the diff:
        git diff HEAD...<branch>
    """
    root = _repo(repo)

    state = load_state(_out_dir(root))
    if state.get("pending_rename_branch") == branch:
        return (
            f"WARNING: branch '{branch}' contains structural moves only — "
            f"placeholder names have not been replaced.\n"
            f"Run apply_rename_map first, then merge the resulting rename branch."
        )

    result = subprocess.run(
        ["git", "-C", str(root), "merge", "--no-ff", branch,
         "-m", f"refactor: merge {branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"FAILED: git merge returned:\n{result.stderr.strip()}"

    _reset_stale_artifacts(_out_dir(root))
    return f"Merged '{branch}' into current branch.\n{result.stdout.strip()}"
