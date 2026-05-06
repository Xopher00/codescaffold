"""Tests for codescaffold.mcp.tools — each tool called as a plain function."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codescaffold.mcp.tools import (
    analyze,
    approve_moves,
    apply,
    discard_sandbox,
    get_cluster_context,
    merge_sandbox,
    reset,
    validate,
)


# ---------------------------------------------------------------------------
# Additional fixture: git repo with Python source (needed for apply/merge)
# ---------------------------------------------------------------------------

@pytest.fixture()
def git_messy_repo(tmp_path: Path) -> Path:
    """A git-tracked version of messy_repo."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

    pkg = tmp_path / "src" / "messy_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "utils.py").write_text("def helper():\n    return 42\n")
    (pkg / "main.py").write_text(
        "from messy_pkg.utils import helper\n\n\ndef run():\n    return helper()\n"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

class TestAnalyzeTool:
    def test_returns_string(self, messy_repo: Path):
        result = analyze(str(messy_repo))
        assert isinstance(result, str)

    def test_result_contains_hash(self, messy_repo: Path):
        result = analyze(str(messy_repo))
        assert "hash:" in result

    def test_result_contains_sections(self, messy_repo: Path):
        result = analyze(str(messy_repo))
        assert "God Nodes" in result
        assert "Community Cohesion" in result
        assert "Move Candidates" in result

    def test_plan_saved_to_disk(self, messy_repo: Path):
        analyze(str(messy_repo))
        plan_path = messy_repo / ".refactor_plan" / "refactor_plan.json"
        assert plan_path.exists()


# ---------------------------------------------------------------------------
# get_cluster_context
# ---------------------------------------------------------------------------

class TestGetClusterContext:
    def test_returns_string(self, messy_repo: Path):
        result = get_cluster_context(0, str(messy_repo))
        assert isinstance(result, str)

    def test_unknown_community_says_not_found(self, messy_repo: Path):
        result = get_cluster_context(9999, str(messy_repo))
        assert "not found" in result or "Available" in result


# ---------------------------------------------------------------------------
# approve_moves
# ---------------------------------------------------------------------------

class TestApproveMoves:
    def test_requires_existing_plan(self, tmp_path: Path):
        result = approve_moves([], str(tmp_path))
        assert "No plan" in result or "analyze" in result.lower()

    def test_appends_approved_move(self, messy_repo: Path):
        analyze(str(messy_repo))
        move = {
            "kind": "symbol",
            "source_file": "src/messy_pkg/utils.py",
            "symbol": "helper",
            "target_file": "src/messy_pkg/dest.py",
        }
        result = approve_moves([move], str(messy_repo))
        assert "Approved 1" in result

    def test_invalid_move_returns_error(self, messy_repo: Path):
        analyze(str(messy_repo))
        result = approve_moves([{"bad": "data"}], str(messy_repo))
        assert "error" in result.lower() or "invalid" in result.lower() or "Validation" in result


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_clears_plan(self, messy_repo: Path):
        analyze(str(messy_repo))
        plan_path = messy_repo / ".refactor_plan" / "refactor_plan.json"
        assert plan_path.exists()
        reset(str(messy_repo))
        assert not plan_path.exists()

    def test_reset_on_empty_dir_does_not_crash(self, tmp_path: Path):
        result = reset(str(tmp_path))
        assert "nothing to remove" in result or "Reset" in result


# ---------------------------------------------------------------------------
# apply + validate + discard (integration, requires git)
# ---------------------------------------------------------------------------

class TestApplyValidateDiscard:
    def test_apply_no_plan_returns_error(self, git_messy_repo: Path):
        result = apply("test-apply-1", str(git_messy_repo))
        assert "No plan" in result

    def test_apply_no_approved_moves_returns_error(self, git_messy_repo: Path):
        analyze(str(git_messy_repo))
        result = apply("test-apply-2", str(git_messy_repo))
        assert "No approved" in result

    def test_full_flow_apply_and_discard(self, git_messy_repo: Path):
        # dest.py must exist before analyze so the graph_hash is stable
        dest = git_messy_repo / "src" / "messy_pkg" / "dest.py"
        dest.write_text("")
        subprocess.run(["git", "add", "."], cwd=git_messy_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add dest.py"], cwd=git_messy_repo, check=True, capture_output=True)

        analyze(str(git_messy_repo))

        move = {
            "kind": "symbol",
            "source_file": "src/messy_pkg/utils.py",
            "symbol": "helper",
            "target_file": "src/messy_pkg/dest.py",
        }
        approval = approve_moves([move], str(git_messy_repo))
        assert "Approved 1" in approval, f"approve_moves failed: {approval}"

        result = apply("e2e-branch", str(git_messy_repo))
        assert isinstance(result, str)
        # Regardless of validation outcome, apply should run without crashing
        assert "Apply result" in result or "ERROR" in result

        # Clean up
        sandbox_path = git_messy_repo / ".worktrees" / "e2e-branch"
        if sandbox_path.exists():
            discard_sandbox("e2e-branch", str(git_messy_repo))

    def test_validate_missing_sandbox(self, git_messy_repo: Path):
        result = validate("no-such-branch", str(git_messy_repo))
        assert "not found" in result


# ---------------------------------------------------------------------------
# Server registration (smoke test — no MCP runtime needed)
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_mcp_object_has_tools(self):
        from codescaffold.mcp.server import mcp
        # FastMCP exposes registered tools
        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "analyze" in tool_names
        assert "approve_moves" in tool_names
        assert "apply" in tool_names
        assert "merge_sandbox" in tool_names

    def test_rope_ops_not_in_tool_surface(self):
        from codescaffold.mcp.server import mcp
        tools = mcp._tool_manager.list_tools()
        tool_names = {t.name for t in tools}
        # Raw rope operations must not be exposed
        assert "move_symbol" not in tool_names
        assert "rename_symbol" not in tool_names
        assert "move_module" not in tool_names
        assert "list_symbols" not in tool_names
