"""Tests for codescaffold.sandbox — git worktree isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codescaffold.sandbox import SandboxError, create_sandbox, discard_sandbox


def _current_branches(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return [b.strip() for b in result.stdout.splitlines() if b.strip()]


class TestCreateSandbox:
    def test_creates_worktree_directory(self, git_repo: Path):
        path = create_sandbox(git_repo, "test-branch-1")
        assert path.exists()
        assert path.is_dir()

    def test_creates_new_branch(self, git_repo: Path):
        create_sandbox(git_repo, "test-branch-2")
        branches = _current_branches(git_repo)
        assert "test-branch-2" in branches

    def test_returns_absolute_path(self, git_repo: Path):
        path = create_sandbox(git_repo, "test-branch-3")
        assert path.is_absolute()

    def test_worktree_path_matches_branch_name(self, git_repo: Path):
        path = create_sandbox(git_repo, "my-feature")
        assert path.name == "my-feature"


class TestDiscardSandbox:
    def test_removes_worktree_directory(self, git_repo: Path):
        path = create_sandbox(git_repo, "discard-me")
        assert path.exists()
        discard_sandbox(git_repo, "discard-me")
        assert not path.exists()

    def test_deletes_branch(self, git_repo: Path):
        create_sandbox(git_repo, "branch-to-delete")
        discard_sandbox(git_repo, "branch-to-delete")
        branches = _current_branches(git_repo)
        assert "branch-to-delete" not in branches

    def test_nonexistent_sandbox_raises(self, git_repo: Path):
        with pytest.raises(SandboxError):
            discard_sandbox(git_repo, "does-not-exist")
