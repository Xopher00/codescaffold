"""Git worktree-based sandbox for isolated refactoring."""

from __future__ import annotations

import subprocess
from pathlib import Path


class SandboxError(Exception):
    """Raised when a git worktree operation fails."""


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _require(result: subprocess.CompletedProcess, op: str) -> None:
    if result.returncode != 0:
        raise SandboxError(f"{op} failed: {result.stderr.strip()}")


def create_sandbox(repo: Path, branch_name: str) -> Path:
    """Create a git worktree at .worktrees/<branch_name> on a new branch.

    Returns the absolute path to the worktree directory.
    """
    repo = Path(repo).resolve()
    worktree_path = repo / ".worktrees" / branch_name
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    result = _git(
        ["worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=repo,
    )
    _require(result, f"git worktree add {branch_name}")
    return worktree_path


def merge_sandbox(repo: Path, branch_name: str) -> None:
    """Merge the sandbox branch into HEAD with --no-ff."""
    repo = Path(repo).resolve()
    result = _git(["merge", "--no-ff", branch_name, "-m", f"refactor: apply sandbox {branch_name}"], cwd=repo)
    _require(result, f"git merge {branch_name}")


def discard_sandbox(repo: Path, branch_name: str) -> None:
    """Remove the worktree directory and delete the sandbox branch."""
    repo = Path(repo).resolve()
    worktree_path = repo / ".worktrees" / branch_name

    # Remove worktree (force in case of unclean state)
    result = _git(["worktree", "remove", "--force", str(worktree_path)], cwd=repo)
    _require(result, f"git worktree remove {branch_name}")

    # Delete the branch
    result = _git(["branch", "-D", branch_name], cwd=repo)
    _require(result, f"git branch -D {branch_name}")
