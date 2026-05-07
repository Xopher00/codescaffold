"""Codescaffold MCP tool implementations.

Each function is a thin orchestration layer over the internal modules.
These functions may be called directly (for testing) or via the MCP server.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codescaffold.audit import ApplyAudit
from codescaffold.bridge import preflight_status, resolve_candidates
from codescaffold.candidates import propose_moves
from codescaffold.contracts import (
    detect_package_cycles, 
    generate_importlinter_config, 
    run_lint_imports, 
    propose_alternatives,
)
from codescaffold.graphify import cohesion, god_nodes, surprises, run_extract
from codescaffold.operations import (
    RenameEntry,
    RopeOperationError,
    close_rope_project,
    move_module,
    move_symbol,
    rename_symbol_batch,
)
from codescaffold.plans import (
    ApprovedMove,
    ApprovedRename,
    Plan,
    RopeResolutionRecord,
    StalePlanError,
    assert_fresh,
    candidates_to_records,
    load,
    save,
)
from codescaffold.sandbox import (
    create_sandbox as _create_sandbox,
    discard_sandbox as _discard_sandbox,
    merge_sandbox as _merge_sandbox,
    SandboxError
)
from codescaffold.validation import run_validation


def _plan_path(repo_path: str) -> Path:
    return Path(repo_path) / ".refactor_plan" / "refactor_plan.json"


def _audit_dir(repo_path: str) -> Path:
    return Path(repo_path) / ".refactor_plan" / "audit"


def _worktree_path(repo_path: str, branch_name: str) -> Path:
    return Path(repo_path).resolve() / ".worktrees" / branch_name


def _commit_in_sandbox(sandbox_path: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=sandbox_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message, "--allow-empty"],
        cwd=sandbox_path, check=True, capture_output=True,
    )


# ---------------------------------------------------------------------------
# Exposed MCP tools
# ---------------------------------------------------------------------------

def analyze(repo_path: str) -> str:
    """Run graphify analysis on a repository, propose refactor candidates, and persist a plan.

    Returns a markdown report summarising god nodes, community cohesion,
    move candidates, and surprising connections.
    """
    snap = run_extract(Path(repo_path))

    candidates = propose_moves(snap)
    resolutions = resolve_candidates(candidates, Path(repo_path))
    records = candidates_to_records(candidates, resolutions=resolutions)
    plan = Plan(graph_hash=snap.graph_hash, candidates=records)
    save(plan, _plan_path(repo_path))

    top_nodes = god_nodes(snap, top_n=5)
    cohesion_scores = cohesion(snap)
    surprise_edges = surprises(snap, top_n=3)

    lines = [f"# Analysis — `{Path(repo_path).name}` (hash: `{snap.graph_hash[:12]}…`)", ""]

    lines += ["## God Nodes (most-connected entities)"]
    if top_nodes:
        for n in top_nodes:
            lines.append(f"- **{n.label}** (degree {n.degree})")
    else:
        lines.append("- *(none found)*")
    lines.append("")

    lines += ["## Community Cohesion"]
    for cid, score in sorted(cohesion_scores.items()):
        nodes = snap.communities.get(cid, [])
        flag = " ⚠ low cohesion" if score < 0.15 else ""
        lines.append(f"- Community {cid} ({len(nodes)} nodes): **{score:.2f}**{flag}")
    lines.append("")

    n_ready = sum(1 for r in records if r.preflight == "ready")
    n_review = sum(1 for r in records if r.preflight == "needs_review")
    n_blocked = sum(1 for r in records if r.preflight == "blocked")
    preflight_summary = f"{n_ready} ready / {n_review} needs_review / {n_blocked} blocked"
    lines += [f"## Move Candidates ({len(candidates)} found — {preflight_summary})"]
    if records:
        for r in records:
            tag = r.preflight or "unknown"
            detail = ""
            if r.resolution and r.resolution.reason:
                detail = f": {r.resolution.reason}"
                if r.resolution.near_misses:
                    detail += f" — did you mean: {', '.join(r.resolution.near_misses)}"
            sym_part = f"`{r.symbol}` in " if r.symbol else ""
            lines.append(
                f"- [{tag}{detail}] {sym_part}`{r.source_file}` → `{r.target_file}` [{r.confidence} confidence]"
            )
            for reason in r.reasons:
                lines.append(f"  - {reason}")
    else:
        lines.append("- *(no candidates — graph is well-structured)*")
    lines.append("")

    lines += ["## Surprising Connections"]
    if surprise_edges:
        for e in surprise_edges:
            lines.append(f"- `{e.source}` ↔ `{e.target}` ({e.reason})")
    else:
        lines.append("- *(none)*")
    lines.append("")

    lines.append(
        f"**Plan saved.** {len(candidates)} candidate(s) ({preflight_summary}). "
        "Run `approve_moves` with your selection to proceed."
    )

    return "\n".join(lines)


def get_cluster_context(community_id: int, repo_path: str) -> str:
    """Return detailed context for a community to support move-approval decisions.

    Shows the nodes in the community, their source files, cohesion, and which
    other communities they connect to.
    """
    snap = run_extract(Path(repo_path))

    nodes = snap.communities.get(community_id)
    if nodes is None:
        return f"Community {community_id} not found. Available: {sorted(snap.communities.keys())}"

    coh = snap.cohesion_scores().get(community_id, 0.0)
    G = snap.graph
    node_to_community = {n: cid for cid, ns in snap.communities.items() for n in ns}

    lines = [f"# Community {community_id} — {len(nodes)} nodes (cohesion: {coh:.2f})", ""]
    lines.append("## Members")
    for node in sorted(nodes):
        attrs = G.nodes.get(node, {})
        label = attrs.get("label", node)
        src = attrs.get("source_file", "?")
        degree = G.degree(node)
        external = [
            node_to_community[nb]
            for nb in G.neighbors(node)
            if nb in node_to_community and node_to_community[nb] != community_id
        ]
        ext_summary = f", connects to community {set(external)}" if external else ""
        lines.append(f"- **{label}** (`{src}`, degree {degree}{ext_summary})")

    return "\n".join(lines)


def approve_moves(moves: list[dict], repo_path: str) -> str:
    """Record agent-approved moves into the persisted plan.

    Each move dict must have: kind, source_file, target_file.
    Symbol moves also require: symbol.

    Performs a freshness check — raises if the graph has changed since analyze.
    """
    plan_path = _plan_path(repo_path)
    if not plan_path.exists():
        return "No plan found. Run `analyze` first."

    plan = load(plan_path)
    snap = run_extract(Path(repo_path))

    try:
        assert_fresh(plan, snap)
    except StalePlanError as e:
        return f"ERROR: {e}"

    approved = []
    errors = []
    for m in moves:
        try:
            approved.append(ApprovedMove.model_validate(m))
        except Exception as exc:
            errors.append(f"Invalid move {m}: {exc}")

    if errors:
        return "Validation errors:\n" + "\n".join(errors)

    candidate_index: dict[tuple[str, str | None, str], object] = {
        (r.source_file, r.symbol, r.target_file): r for r in plan.candidates
    }

    blocked: list[str] = []
    warnings: list[str] = []
    for m in approved:
        record = candidate_index.get((m.source_file, m.symbol, m.target_file))
        if record is None:
            sym = f"`{m.symbol}` in " if m.symbol else ""
            warnings.append(f"  ⚠ no preflight (handcrafted): {sym}`{m.source_file}` → `{m.target_file}`")
        elif record.preflight == "blocked":  # type: ignore[union-attr]
            reason = record.resolution.reason if record.resolution else "unknown"  # type: ignore[union-attr]
            near = ", ".join(record.resolution.near_misses) if record.resolution and record.resolution.near_misses else ""  # type: ignore[union-attr]
            sym = f"`{m.symbol}` in " if m.symbol else ""
            msg = f"  ✗ blocked: {sym}`{m.source_file}` → `{m.target_file}` — {reason}"
            if near:
                msg += f" (did you mean: {near})"
            blocked.append(msg)
        elif record.preflight == "needs_review":  # type: ignore[union-attr]
            reason = record.resolution.reason if record.resolution else ""  # type: ignore[union-attr]
            sym = f"`{m.symbol}` in " if m.symbol else ""
            warnings.append(f"  ⚠ needs_review: {sym}`{m.source_file}` → `{m.target_file}` — {reason}")

    if blocked:
        return "Blocked moves — fix these before approving:\n" + "\n".join(blocked)

    updated = Plan(
        graph_hash=plan.graph_hash,
        candidates=plan.candidates,
        approved_moves=list(plan.approved_moves) + approved,
        created_at=plan.created_at,
    )
    save(updated, plan_path)

    lines = [f"Approved {len(approved)} move(s):"]
    for m in approved:
        sym = f"`{m.symbol}` in " if m.symbol else ""
        lines.append(f"- {m.kind}: {sym}`{m.source_file}` → `{m.target_file}`")
    if warnings:
        lines.append("\nWarnings:")
        lines.extend(warnings)
    lines.append("\nRun `apply` with a branch name to execute.")
    return "\n".join(lines)


def apply(branch_name: str, repo_path: str) -> str:
    """Execute approved moves in a git worktree sandbox.

    Runs compileall + pytest after changes. Returns an audit summary.
    Does NOT merge — call `merge_sandbox` after reviewing the audit.
    """
    plan_path = _plan_path(repo_path)
    if not plan_path.exists():
        return "No plan found. Run `analyze` first."

    plan = load(plan_path)
    if not plan.approved_moves:
        return "No approved moves in plan. Run `approve_moves` first."

    repo = Path(repo_path)

    # Pre-apply contract baseline (only when .importlinter is present)
    pre_apply_passed: bool | None = None
    if (repo / ".importlinter").exists():
        pre_cr = run_lint_imports(repo)
        pre_apply_passed = pre_cr.succeeded

    try:
        sandbox_path = _create_sandbox(repo, branch_name)
    except SandboxError as e:
        return f"ERROR creating sandbox: {e}"

    rope_results = []
    apply_errors = []

    for move in plan.approved_moves:
        try:
            if move.kind == "symbol" and move.symbol:
                result = move_symbol(
                    str(sandbox_path), move.source_file, move.symbol, move.target_file
                )
            elif move.kind == "module":
                result = move_module(str(sandbox_path), move.source_file, move.target_file)
            else:
                apply_errors.append(f"Unsupported move kind: {move.kind}")
                continue
            close_rope_project(str(sandbox_path))
            rope_results.append(result)
        except RopeOperationError as e:
            apply_errors.append(str(e))

    if apply_errors:
        try:
            _discard_sandbox(repo, branch_name)
        except SandboxError:
            pass
        return "Apply failed:\n" + "\n".join(apply_errors)

    try:
        _commit_in_sandbox(sandbox_path, f"refactor: {len(rope_results)} move(s) via codescaffold")
    except subprocess.CalledProcessError as e:
        return f"ERROR committing in sandbox: {e}"

    validation = run_validation(sandbox_path)

    audit = ApplyAudit(
        plan_hash=plan.graph_hash,
        sandbox_branch=branch_name,
        moves_applied=tuple(plan.approved_moves),
        rope_results=tuple(rope_results),
        validation=validation,
        succeeded=validation.succeeded,
    )
    audit.save(_audit_dir(repo_path))

    is_contract_regression = (
        pre_apply_passed is True and not validation.contracts_ok
    )

    lines = [
        f"## Apply result — branch `{branch_name}`",
        f"Moves applied: {len(rope_results)}",
        f"Validation: {'✓ passed' if validation.succeeded else '✗ failed'}",
    ]
    if not validation.succeeded:
        lines.append(f"Failed steps: {', '.join(validation.failed_steps)}")
        if validation.pytest_summary:
            lines.append(f"```\n{validation.pytest_summary[:500]}\n```")

    if is_contract_regression:
        lines += [
            "",
            "⚠ Contract regression — contracts were passing before this apply.",
            "",
            "Two paths to resolve:",
            f"  1. Update the contract to reflect the new structure:",
            f"     → call `update_contract(branch_name=\"{branch_name}\", repo_path=...)`",
            f"  2. See alternative move targets that satisfy the contract:",
            f"     → call `propose_violation_fix(branch_name=\"{branch_name}\", repo_path=...)`",
            "",
            "Or call `discard_sandbox` to abandon.",
        ]
    else:
        lines.append(
            "\nRun `merge_sandbox` to merge, or `discard_sandbox` to abandon."
            if validation.succeeded
            else "\nRun `discard_sandbox` to clean up."
        )
    return "\n".join(lines)


def validate(branch_name: str, repo_path: str) -> str:
    """Re-run compileall + pytest in a sandbox branch."""
    sandbox_path = _worktree_path(repo_path, branch_name)
    if not sandbox_path.exists():
        return f"Sandbox `{branch_name}` not found."
    result = run_validation(sandbox_path)
    return json.dumps({
        "compileall_ok": result.compileall_ok,
        "pytest_ok": result.pytest_ok,
        "succeeded": result.succeeded,
        "failed_steps": list(result.failed_steps),
        "pytest_summary": result.pytest_summary[:1000],
    }, indent=2)


def merge_sandbox(branch_name: str, repo_path: str) -> str:
    """Merge a sandbox branch into HEAD with --no-ff."""
    try:
        _merge_sandbox(Path(repo_path), branch_name)
        return f"Merged `{branch_name}` into HEAD."
    except SandboxError as e:
        return f"ERROR: {e}"


def discard_sandbox(branch_name: str, repo_path: str) -> str:
    """Discard a sandbox branch and remove its worktree."""
    try:
        _discard_sandbox(Path(repo_path), branch_name)
        return f"Discarded sandbox `{branch_name}`."
    except SandboxError as e:
        return f"ERROR: {e}"


def reset(repo_path: str) -> str:
    """Delete the persisted plan and audit records for a repository."""
    plan_path = _plan_path(repo_path)
    removed = []
    if plan_path.exists():
        plan_path.unlink()
        removed.append("plan")
    return f"Reset complete. Removed: {', '.join(removed) or 'nothing to remove'}."


def contracts(repo_path: str) -> str:
    """Generate and persist import-linter .importlinter config from current graph communities.

    If cycles are detected, refuses to emit the contract and instead returns
    cycle reports with MoveCandidate suggestions for breaking them.
    """
    snap = run_extract(Path(repo_path))
    artifact = generate_importlinter_config(Path(repo_path), snap)

    if not artifact.written:
        lines = [
            "# Contracts — cycles detected",
            "",
            f"{len(artifact.cycles_detected)} cycle(s) prevent contract generation.",
            "Resolve them first, then re-run `contracts`.",
            "",
            "## Detected cycles",
        ]
        for cr in artifact.cycles_detected:
            lines.append(f"- `{' → '.join(cr.cycle)} → {cr.cycle[0]}`")
            if cr.suggested_break:
                sb = cr.suggested_break
                lines.append(
                    f"  ↳ Suggested break: move `{sb.symbol}` from "
                    f"`{sb.source_file}` → `{sb.target_file}`"
                )
        lines += [
            "",
            "Approve cycle-break move(s) with `approve_moves`, then run `apply`.",
        ]
        return "\n".join(lines)

    lines = [
        f"# Contracts generated — `{Path(repo_path).name}`",
        f"Written to: `{artifact.config_path}`",
        "",
        "## Layers",
    ]
    for i, layer in enumerate(reversed(artifact.layers)):
        lines.append(f"  Layer {i}: {' | '.join(sorted(layer))}")
    if artifact.forbidden:
        lines += ["", "## Forbidden connections"]
        for src, tgt in artifact.forbidden:
            lines.append(f"  - `{src}` → `{tgt}`")
    lines += ["", "Run `validate_contracts` to verify the generated contract."]
    return "\n".join(lines)


def validate_contracts(repo_path: str) -> str:
    """Run lint-imports against the repo's .importlinter and return a formatted report."""
    result = run_lint_imports(Path(repo_path))
    status = "✓ passed" if result.succeeded else "✗ failed"
    lines = [
        f"# Contract validation — {status}",
        f"Contracts checked: {result.contracts_checked}",
        f"Contracts failed: {result.contracts_failed}",
    ]
    if result.raw_output and result.raw_output != "(no .importlinter)":
        lines += ["", "```", result.raw_output.strip(), "```"]
    elif result.raw_output == "(no .importlinter)":
        lines.append("No .importlinter found — run `contracts` first.")
    return "\n".join(lines)


def update_contract(branch_name: str, repo_path: str) -> str:
    """Regenerate .importlinter from a sandbox's post-move state.

    Refuses if the sandbox has introduced new cycles (preserves the
    'contracts = acyclic graph' invariant). Commits the updated contract
    into the sandbox.
    """
    sandbox_path = _worktree_path(repo_path, branch_name)
    if not sandbox_path.exists():
        return f"Sandbox `{branch_name}` not found."

    snap = run_extract(sandbox_path)
    artifact = generate_importlinter_config(sandbox_path, snap)

    if not artifact.written:
        lines = [
            f"ERROR: Cannot update contract — {len(artifact.cycles_detected)} cycle(s) detected "
            "in the sandbox after the moves.",
            "Resolve cycles before regenerating the contract.",
        ]
        for cr in artifact.cycles_detected:
            lines.append(f"  - `{' → '.join(cr.cycle)} → {cr.cycle[0]}`")
        return "\n".join(lines)

    try:
        _commit_in_sandbox(sandbox_path, "contracts: regenerate .importlinter via codescaffold")
    except subprocess.CalledProcessError as e:
        return f"ERROR committing contract update: {e}"

    return (
        f"Contract updated and committed in `{branch_name}`.\n"
        f"Layers: {len(artifact.layers)} | Forbidden: {len(artifact.forbidden)}\n"
        "Run `validate_contracts` on the sandbox or `merge_sandbox` to merge."
    )


def propose_violation_fix(branch_name: str, repo_path: str) -> str:
    """Propose alternative move targets for moves that violated import contracts.

    Reads the current plan's approved moves and the sandbox's contract state,
    then returns MoveCandidate suggestions for targets that satisfy the contract.
    """
    sandbox_path = _worktree_path(repo_path, branch_name)
    if not sandbox_path.exists():
        return f"Sandbox `{branch_name}` not found."

    plan_path = _plan_path(repo_path)
    if not plan_path.exists():
        return "No plan found. Run `analyze` first."

    plan = load(plan_path)
    if not plan.approved_moves:
        return "No approved moves in plan."

    snap = run_extract(Path(repo_path))

    from codescaffold.contracts.package_graph import build_package_dag
    import networkx as nx
    dag = build_package_dag(snap)
    raw_layers = list(nx.topological_generations(dag))
    layers = tuple(tuple(layer) for layer in raw_layers)

    alternatives = propose_alternatives(
        failed_moves=tuple(plan.approved_moves),
        snap=snap,
        layers=layers,
    )

    if not alternatives:
        return (
            "No alternative targets found. "
            "Consider updating the contract with `update_contract` instead."
        )

    lines = [
        f"# Violation fix proposals — {len(alternatives)} alternative(s)",
        "",
        "Discard the current sandbox, then `approve_moves` with the alternatives below:",
        "",
    ]
    for alt in alternatives:
        sym = f"`{alt.symbol}` in " if alt.symbol else ""
        lines.append(f"- {alt.kind}: {sym}`{alt.source_file}` → `{alt.target_file}` [{alt.confidence}]")
        for r in alt.reasons:
            lines.append(f"  - {r}")
    return "\n".join(lines)


def apply_rename_map(
    repo_path: str,
    branch_name: str,
    rename_map: dict[str, dict[str, str]],
) -> str:
    """Batch-rename symbols across the repo in a sandbox.

    rename_map shape:
        {
            "src/pkg/foo.py": {"OldClass": "NewClass", "old_fn": "new_fn"},
        }

    Preflights each rename via bridge before creating a sandbox. Blocked
    symbols abort the whole batch; needs_review symbols warn and proceed.
    Uses a single rope project session for all renames.
    Does NOT merge — call `merge_sandbox` after reviewing the result.
    """
    if not rename_map:
        return "ERROR: rename_map is empty — nothing to rename."

    entries_flat: list[ApprovedRename] = []
    for file_path, renames in rename_map.items():
        if not isinstance(renames, dict):
            return f"ERROR: rename_map['{file_path}'] must be a dict of {{old: new}}."
        for old_name, new_name in renames.items():
            if not old_name or not new_name:
                return f"ERROR: empty old_name or new_name in rename_map['{file_path}']."
            entries_flat.append(
                ApprovedRename(file_path=file_path, old_name=old_name, new_name=new_name)
            )

    # Adapter satisfying bridge._Candidate Protocol (kind, source_file, symbol)
    class _RenameAdapter:
        kind = "symbol"

        def __init__(self, entry: ApprovedRename) -> None:
            self.source_file = entry.file_path
            self.symbol = entry.old_name

    proto_candidates = [_RenameAdapter(e) for e in entries_flat]
    resolutions = resolve_candidates(proto_candidates, Path(repo_path))

    stamped: list[ApprovedRename] = []
    for entry, res in zip(entries_flat, resolutions):
        pf = preflight_status(res)
        rec = RopeResolutionRecord(
            status=res.status,
            symbol_kind=res.symbol_kind,
            line=res.line,
            near_misses=list(res.near_misses),
            reason=res.reason,
        )
        stamped.append(
            ApprovedRename(
                file_path=entry.file_path,
                old_name=entry.old_name,
                new_name=entry.new_name,
                resolution=rec,
                preflight=pf,
            )
        )

    blocked = [e for e in stamped if e.preflight == "blocked"]
    if blocked:
        lines = ["## Blocked renames — fix these before proceeding:", ""]
        for e in blocked:
            reason = e.resolution.reason if e.resolution else "unknown"
            near = (
                f" (did you mean: {', '.join(e.resolution.near_misses)})"
                if e.resolution and e.resolution.near_misses
                else ""
            )
            lines.append(f"  ✗ `{e.old_name}` in `{e.file_path}` — {reason}{near}")
        return "\n".join(lines)

    warnings_list = [
        f"  ⚠ needs_review: `{e.old_name}` in `{e.file_path}`"
        + (f" — {e.resolution.reason}" if e.resolution and e.resolution.reason else "")
        for e in stamped
        if e.preflight == "needs_review"
    ]

    snap = run_extract(Path(repo_path))
    plan = Plan(graph_hash=snap.graph_hash, approved_renames=stamped)
    save(plan, _plan_path(repo_path))

    repo = Path(repo_path)
    pre_apply_passed: bool | None = None
    if (repo / ".importlinter").exists():
        pre_cr = run_lint_imports(repo)
        pre_apply_passed = pre_cr.succeeded

    try:
        sandbox_path = _create_sandbox(repo, branch_name)
    except SandboxError as e:
        return f"ERROR creating sandbox: {e}"

    rename_entries = [
        RenameEntry(file_path=e.file_path, old_name=e.old_name, new_name=e.new_name)
        for e in stamped
    ]
    batch = rename_symbol_batch(rename_entries, str(sandbox_path))

    if batch.error:
        try:
            _discard_sandbox(repo, branch_name)
        except SandboxError:
            pass
        return (
            f"## Rename failed after {len(batch.applied)}/{len(rename_entries)} rename(s)\n\n"
            f"Error: {batch.error}\n\nSandbox discarded."
        )

    try:
        _commit_in_sandbox(
            sandbox_path,
            f"refactor: {len(batch.applied)} rename(s) via codescaffold",
        )
    except subprocess.CalledProcessError as e:
        return f"ERROR committing in sandbox: {e}"

    validation = run_validation(sandbox_path)

    audit = ApplyAudit(
        plan_hash=plan.graph_hash,
        sandbox_branch=branch_name,
        moves_applied=(),
        rope_results=tuple(batch.rope_results),
        validation=validation,
        succeeded=validation.succeeded,
        renames_applied=tuple(stamped),
    )
    audit.save(_audit_dir(repo_path))

    is_contract_regression = pre_apply_passed is True and not validation.contracts_ok

    lines = [
        f"## Rename result — branch `{branch_name}`",
        f"Renames applied: {len(batch.applied)}",
        f"Validation: {'✓ passed' if validation.succeeded else '✗ failed'}",
    ]
    if not validation.succeeded:
        lines.append(f"Failed steps: {', '.join(validation.failed_steps)}")
        if validation.pytest_summary:
            lines.append(f"```\n{validation.pytest_summary[:500]}\n```")
    if warnings_list:
        lines += ["", "Warnings (needs_review — proceed with care):"] + warnings_list

    if is_contract_regression:
        lines += [
            "",
            "⚠ Contract regression — contracts were passing before this apply.",
            "",
            "Two paths to resolve:",
            "  1. Update the contract to reflect the new structure:",
            f"     → call `update_contract(branch_name=\"{branch_name}\", repo_path=...)`",
            "  2. See alternative move targets that satisfy the contract:",
            f"     → call `propose_violation_fix(branch_name=\"{branch_name}\", repo_path=...)`",
            "",
            "Or call `discard_sandbox` to abandon.",
        ]
    else:
        lines.append(
            "\nRun `merge_sandbox` to merge, or `discard_sandbox` to abandon."
            if validation.succeeded
            else "\nRun `discard_sandbox` to clean up."
        )
    return "\n".join(lines)
