"""Codescaffold MCP tool implementations.

Each function is a thin orchestration layer over the internal modules.
These functions may be called directly (for testing) or via the MCP server.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codescaffold.audit.record import ApplyAudit
from codescaffold.candidates.propose import propose_moves
from codescaffold.contracts.cycles import detect_package_cycles
from codescaffold.contracts.generator import generate_importlinter_config
from codescaffold.contracts.validator import run_lint_imports
from codescaffold.contracts.violation_fix import propose_alternatives
from codescaffold.graphify.analysis import cohesion, god_nodes, surprises
from codescaffold.graphify.extract import run_extract
from codescaffold.operations import (
    RopeOperationError,
    close_rope_project,
    move_module,
    move_symbol,
)
from codescaffold.plans.schema import ApprovedMove, Plan
from codescaffold.plans.store import (
    StalePlanError,
    assert_fresh,
    candidates_to_records,
    load,
    save,
)
from codescaffold.sandbox.worktree import SandboxError
from codescaffold.sandbox.worktree import create_sandbox as _create_sandbox
from codescaffold.sandbox.worktree import discard_sandbox as _discard_sandbox
from codescaffold.sandbox.worktree import merge_sandbox as _merge_sandbox
from codescaffold.validation.runner import run_validation


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
    records = candidates_to_records(candidates)
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

    lines += [f"## Move Candidates ({len(candidates)} found)"]
    if candidates:
        for c in candidates:
            lines.append(
                f"- `{c.symbol}` in `{c.source_file}` → `{c.target_file}` [{c.confidence} confidence]"
            )
            for reason in c.reasons:
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
        f"**Plan saved.** {len(candidates)} candidate(s). "
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
        lines += ["", "```", result.raw_output.strip()[:2000], "```"]
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
