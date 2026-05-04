import hashlib
import json
import os
from pathlib import Path
from .execution import ApplyResult
from .planning import RefactorPlan
from .applicator import _all_imported_modules, _file_to_module, _find_symbol_code, _remove_symbol


_OUT_DIR = ".refactor_plan"

def _repo(repo: str) -> Path:
    """Resolve repo path from argument or CODESCAFFOLD_REPO env var."""
    path = repo or os.environ.get("CODESCAFFOLD_REPO", "")
    if not path:
        raise ValueError(
            "repo argument is required (or set CODESCAFFOLD_REPO env var)"
        )
    return Path(path).resolve()



def _out_dir(root: Path) -> Path:
    return root / _OUT_DIR



def _plan_path(root: Path) -> Path:
    return _out_dir(root) / "refactor_plan.json"



def _load_plan(root: Path) -> RefactorPlan:
    path = _plan_path(root)
    if not path.exists():
        raise FileNotFoundError(f"No plan at {path} — run analyze first")
    return RefactorPlan.model_validate_json(path.read_text(encoding="utf-8"))



def _summarise_result(result: ApplyResult) -> str:
    lines = [
        f"Applied: {len(result.applied)}  "
        f"Failed: {len(result.failed)}  "
        f"Skipped: {len(result.skipped)}"
    ]
    for e in result.failed + result.skipped:
        lines.append(f"  [{e.category}] {e.source}: {e.reason}")
    return "\n".join(lines)



def _format_validation(report: object) -> str:
    lines = []
    for cmd in report.commands:  # type: ignore[union-attr]
        mark = "OK" if cmd.exit_code == 0 else "FAIL"
        lines.append(f"  [{mark}] {cmd.command}")
        if cmd.exit_code != 0:
            if cmd.stdout:
                lines.append(cmd.stdout[-600:])
            if cmd.stderr:
                lines.append(cmd.stderr[:400])
    return "\n".join(lines)



def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    except OSError:
        return None



def _cluster_for_file(plan: "RefactorPlan | None", rel_path: str) -> dict:
    """Return graph metrics for rel_path from the plan, or {} if unavailable."""
    if plan is None:
        return {}
    for cluster in plan.clusters:
        if any(rel_path in sf for sf in cluster.source_files):
            return {
                "community": f"comm_{cluster.community_id}",
                "cohesion": round(cluster.cohesion, 3) if cluster.cohesion is not None else None,
            }
    return {}



def _build_trace(root: Path, tool: str, **fields) -> str:
    """Return a Codescaffold-Trace git trailer line with structured metadata."""
    trace: dict = {"tool": tool}
    trace.update({k: v for k, v in fields.items() if v is not None})

    plan_path = _plan_path(root)
    if (h := _file_hash(plan_path)):
        trace["plan_hash"] = h

    graph_candidates = list(root.glob("graphify-out/graph*.json")) + list(root.glob("**/graph.json"))
    if graph_candidates:
        if (h := _file_hash(graph_candidates[0])):
            trace["graph_hash"] = h

    return f"Codescaffold-Trace: {json.dumps(trace, separators=(',', ':'))}"



def _sandbox_result(branch: str, summary: str) -> str:
    return (
        f"{summary}\n\n"
        f"Validation PASSED. Changes committed to branch '{branch}'.\n"
        f"Review : git diff HEAD...{branch}\n"
        f"Apply  : git merge {branch}\n"
        f"Discard: git branch -D {branch}"
    )



def _check_circular_import_risks(
    src_path: Path,
    dest_path: Path,
    symbol_name: str,
    repo_root: Path,
) -> list[str]:
    """Return risk descriptions for circular imports; empty list means safe."""
    import libcst as cst

    risks: list[str] = []
    try:
        src_tree = cst.parse_module(src_path.read_text(encoding="utf-8"))
    except Exception:
        return risks

    symbol_code = _find_symbol_code(src_tree, symbol_name)
    if symbol_code is None:
        return risks

    dest_module = _file_to_module(dest_path, repo_root)
    src_module = _file_to_module(src_path, repo_root)

    # Case 1 (removed): "symbol uses names defined in dest_module" is not a risk.
    # apply_symbol_move already drops those imports — they become same-file calls.

    # Case 2: dest already imports src AND symbol is still referenced in src
    # after removal — back-import would then create a cycle.
    # If the symbol is not referenced in the source after removal, no
    # back-import fires and there is no cycle.
    if dest_path.exists() and src_module:
        dest_imports = _all_imported_modules(dest_path)
        colliding = [
            m for m in dest_imports
            if m == src_module or m.startswith(src_module + ".")
        ]
        if colliding:
            # Only a real cycle if add_back_import would fire — which it does
            # when the symbol name still appears in the source after removal.
            # Simulate exactly what add_back_import checks.
            try:
                import libcst as cst
                src_text = src_path.read_text(encoding="utf-8")
                src_tree = cst.parse_module(src_text)
                modified = _remove_symbol(src_tree, symbol_name).code
                still_referenced = symbol_name in modified
            except Exception:
                still_referenced = True  # conservative
            if still_referenced:
                risks.append(
                    f"Reverse dependency: destination '{dest_path.name}' already imports "
                    f"from '{src_module}' ({', '.join(colliding)}). "
                    f"Moving '{symbol_name}' there would create a cycle."
                )

    return risks



def _reset_stale_artifacts(out_dir: Path) -> None:
    """Remove plan, state, and import-linter contracts after a successful merge."""
    for name in ("refactor_plan.json", "state.json"):
        p = out_dir / name
        if p.exists():
            p.unlink()
    importlinter = out_dir.parent / ".importlinter"
    if importlinter.exists():
        importlinter.unlink()
