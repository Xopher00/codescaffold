from __future__ import annotations

import subprocess
import time
from pathlib import Path

from refactor_plan.planning.planner import RefactorPlan


def create_worktree(repo_root: Path) -> tuple[Path, str]:
    """Create a git worktree on a fresh branch; return (worktree_path, branch_name)."""
    ts = int(time.time())
    branch = f"refactor/sandbox-{ts}"
    wt_path = Path(f"/tmp/codescaffold_{ts}")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", str(wt_path), "-b", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")
    return wt_path, branch


def commit_and_release(repo_root: Path, wt_path: Path, message: str) -> None:
    """Stage all changes in worktree, commit, then remove the worktree directory.

    The branch is kept so the caller can review and merge it.
    """
    subprocess.run(
        ["git", "-C", str(wt_path), "add", "-A"],
        check=True, capture_output=True,
    )
    r = subprocess.run(
        ["git", "-C", str(wt_path), "commit", "-m", message],
        capture_output=True, text=True,
    )
    if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
        raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True,
    )


def discard_worktree(repo_root: Path, wt_path: Path, branch: str) -> None:
    """Remove the worktree directory and delete the branch — discards all changes."""
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "branch", "-D", branch],
        capture_output=True,
    )


def translate_plan(plan: RefactorPlan, old_root: Path, new_root: Path) -> RefactorPlan:
    """Return a copy of plan with all absolute paths rewritten from old_root to new_root."""
    raw = plan.model_dump_json()
    return RefactorPlan.model_validate_json(raw.replace(str(old_root), str(new_root)))
