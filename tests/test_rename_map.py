"""Tests for rename_ops batch logic and the apply_rename_map MCP tool."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from codescaffold.operations.rename_ops import BatchRenameResult, RenameEntry, rename_symbol_batch
from codescaffold.operations.errors import RopeOperationError
from codescaffold.operations.results import RopeChangeResult


FIXTURES = Path(__file__).parent / "fixtures" / "rename_repo"


@pytest.fixture()
def rename_repo(tmp_path: Path) -> Path:
    """Git-initialised copy of tests/fixtures/rename_repo/ for rope."""
    shutil.copytree(FIXTURES, tmp_path / "rename_repo")
    repo = tmp_path / "rename_repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
    )
    return repo


def _ok_result(files: list[str] | None = None) -> RopeChangeResult:
    return RopeChangeResult(
        changed_files=tuple(files or ["sample.py"]),
        new_path=None,
        manually_fixed_files=(),
        warnings=(),
    )


def _patch_rename(side_effect=None, return_value=None):
    if side_effect is not None:
        return patch("codescaffold.operations.rename_ops.rename_symbol", side_effect=side_effect)
    return patch(
        "codescaffold.operations.rename_ops.rename_symbol",
        return_value=return_value or _ok_result(),
    )


def _patch_close():
    return patch("codescaffold.operations.rename_ops.close_rope_project")


# ---------------------------------------------------------------------------
# rename_symbol_batch — unit tests (mocked rename_symbol)
# ---------------------------------------------------------------------------

def test_batch_renames_all_entries():
    entries = [
        RenameEntry("a.py", "Foo", "Bar"),
        RenameEntry("b.py", "baz", "qux"),
        RenameEntry("c.py", "Old", "New"),
    ]
    with _patch_rename(return_value=_ok_result()), _patch_close():
        result = rename_symbol_batch(entries, "/repo")

    assert result.error is None
    assert len(result.applied) == 3
    assert len(result.rope_results) == 3


def test_batch_stops_on_first_error():
    results_iter = iter([_ok_result(), RopeOperationError("rename", "not found", {})])

    def _side(project_path, file_path, old_name, new_name):
        val = next(results_iter)
        if isinstance(val, Exception):
            raise val
        return val

    entries = [
        RenameEntry("a.py", "A", "AA"),
        RenameEntry("b.py", "B", "BB"),
        RenameEntry("c.py", "C", "CC"),
    ]
    with _patch_rename(side_effect=_side), _patch_close():
        result = rename_symbol_batch(entries, "/repo")

    assert len(result.applied) == 1
    assert result.applied[0].old_name == "A"
    assert result.error is not None
    assert "B" in result.error


def test_batch_close_called_once():
    entries = [RenameEntry("a.py", "X", "Y"), RenameEntry("b.py", "P", "Q")]
    with _patch_rename(return_value=_ok_result()), _patch_close() as mock_close:
        rename_symbol_batch(entries, "/repo")

    mock_close.assert_called_once_with("/repo")


def test_batch_close_called_on_exception():
    def _fail(*args, **kwargs):
        raise RopeOperationError("rename", "explode", {})

    entries = [RenameEntry("a.py", "X", "Y")]
    with _patch_rename(side_effect=_fail), _patch_close() as mock_close:
        result = rename_symbol_batch(entries, "/repo")

    mock_close.assert_called_once()
    assert result.error is not None


def test_batch_empty_list():
    with _patch_rename(return_value=_ok_result()) as mock_rename, _patch_close() as mock_close:
        result = rename_symbol_batch([], "/repo")

    mock_rename.assert_not_called()
    mock_close.assert_called_once()
    assert result.error is None
    assert result.applied == ()
    assert result.rope_results == ()


# ---------------------------------------------------------------------------
# Integration — real rope (rename_repo fixture)
# ---------------------------------------------------------------------------

def test_real_rename_updates_definition(rename_repo: Path):
    entries = [RenameEntry("sample.py", "OldClass", "NewClass")]
    result = rename_symbol_batch(entries, str(rename_repo))

    assert result.error is None
    content = (rename_repo / "sample.py").read_text()
    assert "class NewClass" in content
    assert "OldClass" not in content


def test_real_rename_updates_caller(rename_repo: Path):
    entries = [RenameEntry("sample.py", "OldClass", "NewClass")]
    rename_symbol_batch(entries, str(rename_repo))

    caller = (rename_repo / "caller.py").read_text()
    assert "NewClass" in caller
    assert "OldClass" not in caller


def test_real_two_renames_one_session(rename_repo: Path):
    entries = [
        RenameEntry("sample.py", "OldClass", "NewClass"),
        RenameEntry("sample.py", "old_function", "new_function"),
    ]
    with patch("codescaffold.operations.rename_ops.close_rope_project", wraps=lambda p: None) as mock_close:
        result = rename_symbol_batch(entries, str(rename_repo))

    assert result.error is None
    assert len(result.applied) == 2
    mock_close.assert_called_once()

    sample = (rename_repo / "sample.py").read_text()
    assert "class NewClass" in sample
    assert "def new_function" in sample

    caller = (rename_repo / "caller.py").read_text()
    assert "NewClass" in caller
    assert "new_function" in caller


def test_real_rename_not_found(rename_repo: Path):
    entries = [RenameEntry("sample.py", "NonExistentSymbol", "Whatever")]
    result = rename_symbol_batch(entries, str(rename_repo))

    assert result.error is not None
    assert result.applied == ()


# ---------------------------------------------------------------------------
# apply_rename_map MCP tool — gate tests (mocked sandbox + validation)
# ---------------------------------------------------------------------------

from codescaffold.validation.runner import ValidationResult
from codescaffold.mcp.tools import apply_rename_map


def _ok_validation(contracts_ok: bool = True) -> ValidationResult:
    return ValidationResult(
        compileall_ok=True,
        pytest_ok=True,
        pytest_summary="1 passed",
        failed_steps=(),
        contracts_ok=contracts_ok,
    )


def _fail_validation() -> ValidationResult:
    return ValidationResult(
        compileall_ok=False,
        pytest_ok=False,
        pytest_summary="1 failed",
        failed_steps=("compileall",),
        contracts_ok=True,
    )


def _mock_snap(graph_hash: str = "abc123") -> MagicMock:
    snap = MagicMock()
    snap.graph_hash = graph_hash
    return snap


def _mock_resolution(status: str = "resolved") -> MagicMock:
    res = MagicMock()
    res.status = status
    res.symbol_kind = "class" if status == "resolved" else None
    res.line = 1 if status == "resolved" else None
    res.near_misses = ("OldClass",) if status == "not_found" else ()
    res.reason = None if status == "resolved" else f"symbol not found"
    return res


def test_tool_empty_rename_map():
    result = apply_rename_map("/repo", "branch", {})
    assert "ERROR" in result
    assert "empty" in result.lower()


def test_tool_blocked_preflight_no_sandbox(tmp_path: Path):
    blocked_res = _mock_resolution("not_found")

    with (
        patch("codescaffold.mcp.tools.run_extract", return_value=_mock_snap()),
        patch("codescaffold.mcp.tools.resolve_candidates", return_value=[blocked_res]),
        patch("codescaffold.mcp.tools.preflight_status", return_value="blocked"),
        patch("codescaffold.mcp.tools._create_sandbox") as mock_sandbox,
    ):
        result = apply_rename_map(
            str(tmp_path), "branch", {"sample.py": {"NonExistent": "Whatever"}}
        )

    mock_sandbox.assert_not_called()
    assert "Blocked" in result or "blocked" in result


def test_tool_needs_review_proceeds_with_warning(tmp_path: Path):
    review_res = _mock_resolution("not_found")
    review_res.status = "ambiguous"
    review_res.reason = "2 matches"
    review_res.near_misses = ()

    sandbox_path = tmp_path / ".worktrees" / "branch"
    sandbox_path.mkdir(parents=True)

    with (
        patch("codescaffold.mcp.tools.run_extract", return_value=_mock_snap()),
        patch("codescaffold.mcp.tools.resolve_candidates", return_value=[review_res]),
        patch("codescaffold.mcp.tools.save"),
        patch("codescaffold.mcp.tools._create_sandbox", return_value=sandbox_path),
        patch("codescaffold.mcp.tools.rename_symbol_batch", return_value=BatchRenameResult(
            applied=(RenameEntry("sample.py", "OldClass", "NewClass"),),
            rope_results=(_ok_result(),),
        )),
        patch("codescaffold.mcp.tools._commit_in_sandbox"),
        patch("codescaffold.mcp.tools.run_validation", return_value=_ok_validation()),
        patch("codescaffold.mcp.tools.ApplyAudit") as mock_audit,
    ):
        mock_audit.return_value.save = MagicMock()
        result = apply_rename_map(
            str(tmp_path), "branch", {"sample.py": {"OldClass": "NewClass"}}
        )

    assert "needs_review" in result or "⚠" in result
    assert "Rename result" in result


def test_tool_success_audit_written(tmp_path: Path):
    sandbox_path = tmp_path / ".worktrees" / "branch"
    sandbox_path.mkdir(parents=True)
    resolved_res = _mock_resolution("resolved")

    with (
        patch("codescaffold.mcp.tools.run_extract", return_value=_mock_snap()),
        patch("codescaffold.mcp.tools.resolve_candidates", return_value=[resolved_res]),
        patch("codescaffold.mcp.tools.save"),
        patch("codescaffold.mcp.tools._create_sandbox", return_value=sandbox_path),
        patch("codescaffold.mcp.tools.rename_symbol_batch", return_value=BatchRenameResult(
            applied=(RenameEntry("sample.py", "OldClass", "NewClass"),),
            rope_results=(_ok_result(),),
        )),
        patch("codescaffold.mcp.tools._commit_in_sandbox"),
        patch("codescaffold.mcp.tools.run_validation", return_value=_ok_validation()),
        patch("codescaffold.mcp.tools.ApplyAudit") as mock_audit_cls,
    ):
        mock_audit_instance = MagicMock()
        mock_audit_cls.return_value = mock_audit_instance
        result = apply_rename_map(
            str(tmp_path), "branch", {"sample.py": {"OldClass": "NewClass"}}
        )

    mock_audit_instance.save.assert_called_once()
    assert "Rename result" in result
    assert "✓ passed" in result


def test_tool_returns_apply_style_markdown(tmp_path: Path):
    sandbox_path = tmp_path / ".worktrees" / "branch"
    sandbox_path.mkdir(parents=True)
    resolved_res = _mock_resolution("resolved")

    with (
        patch("codescaffold.mcp.tools.run_extract", return_value=_mock_snap()),
        patch("codescaffold.mcp.tools.resolve_candidates", return_value=[resolved_res]),
        patch("codescaffold.mcp.tools.save"),
        patch("codescaffold.mcp.tools._create_sandbox", return_value=sandbox_path),
        patch("codescaffold.mcp.tools.rename_symbol_batch", return_value=BatchRenameResult(
            applied=(RenameEntry("sample.py", "OldClass", "NewClass"),),
            rope_results=(_ok_result(),),
        )),
        patch("codescaffold.mcp.tools._commit_in_sandbox"),
        patch("codescaffold.mcp.tools.run_validation", return_value=_ok_validation()),
        patch("codescaffold.mcp.tools.ApplyAudit") as mock_audit_cls,
    ):
        mock_audit_cls.return_value.save = MagicMock()
        result = apply_rename_map(
            str(tmp_path), "branch", {"sample.py": {"OldClass": "NewClass"}}
        )

    assert result.startswith("## Rename result")
    assert "Renames applied: 1" in result
    assert "merge_sandbox" in result


def test_tool_contract_regression_block(tmp_path: Path):
    sandbox_path = tmp_path / ".worktrees" / "branch"
    sandbox_path.mkdir(parents=True)
    (tmp_path / ".importlinter").write_text("[importlinter]\n")
    resolved_res = _mock_resolution("resolved")

    pre_cr = MagicMock()
    pre_cr.succeeded = True

    with (
        patch("codescaffold.mcp.tools.run_extract", return_value=_mock_snap()),
        patch("codescaffold.mcp.tools.resolve_candidates", return_value=[resolved_res]),
        patch("codescaffold.mcp.tools.save"),
        patch("codescaffold.mcp.tools.run_lint_imports", return_value=pre_cr),
        patch("codescaffold.mcp.tools._create_sandbox", return_value=sandbox_path),
        patch("codescaffold.mcp.tools.rename_symbol_batch", return_value=BatchRenameResult(
            applied=(RenameEntry("sample.py", "OldClass", "NewClass"),),
            rope_results=(_ok_result(),),
        )),
        patch("codescaffold.mcp.tools._commit_in_sandbox"),
        patch("codescaffold.mcp.tools.run_validation", return_value=_ok_validation(contracts_ok=False)),
        patch("codescaffold.mcp.tools.ApplyAudit") as mock_audit_cls,
    ):
        mock_audit_cls.return_value.save = MagicMock()
        result = apply_rename_map(
            str(tmp_path), "branch", {"sample.py": {"OldClass": "NewClass"}}
        )

    assert "Contract regression" in result
    assert "update_contract" in result


def test_tool_batch_failure_discards_sandbox(tmp_path: Path):
    sandbox_path = tmp_path / ".worktrees" / "branch"
    sandbox_path.mkdir(parents=True)
    resolved_res = _mock_resolution("resolved")

    with (
        patch("codescaffold.mcp.tools.run_extract", return_value=_mock_snap()),
        patch("codescaffold.mcp.tools.resolve_candidates", return_value=[resolved_res, resolved_res]),
        patch("codescaffold.mcp.tools.save"),
        patch("codescaffold.mcp.tools._create_sandbox", return_value=sandbox_path),
        patch("codescaffold.mcp.tools.rename_symbol_batch", return_value=BatchRenameResult(
            applied=(RenameEntry("sample.py", "OldClass", "NewClass"),),
            rope_results=(_ok_result(),),
            error="second rename failed",
        )),
        patch("codescaffold.mcp.tools._discard_sandbox") as mock_discard,
    ):
        result = apply_rename_map(
            str(tmp_path), "branch",
            {"sample.py": {"OldClass": "NewClass", "old_function": "new_function"}},
        )

    mock_discard.assert_called_once()
    assert "failed" in result.lower()
    assert "discarded" in result.lower() or "Sandbox discarded" in result


# ---------------------------------------------------------------------------
# Schema / load-compat
# ---------------------------------------------------------------------------

import json
from codescaffold.plans.schema import Plan


def test_plan_loads_without_approved_renames():
    data = {"graph_hash": "abc", "created_at": "2026-01-01T00:00:00+00:00"}
    plan = Plan.model_validate(data)
    assert plan.approved_renames == []


def test_plan_approved_renames_serialises_when_empty():
    plan = Plan(graph_hash="xyz")
    parsed = json.loads(plan.model_dump_json())
    assert parsed["approved_renames"] == []
