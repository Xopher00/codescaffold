"""Tests for codescaffold.validation and codescaffold.audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codescaffold.audit import ApplyAudit
from codescaffold.operations.results import RopeChangeResult
from codescaffold.plans.schema import ApprovedMove
from codescaffold.validation import ValidationResult, run_validation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def valid_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "utils.py").write_text("def helper():\n    return 42\n")
    return tmp_path


@pytest.fixture()
def broken_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "bad.py").write_text("def oops(\n")  # syntax error
    return tmp_path


@pytest.fixture()
def repo_with_passing_tests(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "utils.py").write_text("def helper():\n    return 42\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_passes():\n    assert 1 + 1 == 2\n")
    return tmp_path


@pytest.fixture()
def repo_with_failing_tests(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fail.py").write_text("def test_fails():\n    assert False\n")
    return tmp_path


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class TestRunValidation:
    def test_valid_repo_succeeds(self, valid_repo: Path):
        result = run_validation(valid_repo)
        assert result.compileall_ok is True
        assert result.succeeded is True

    def test_syntax_error_fails_compileall(self, broken_repo: Path):
        result = run_validation(broken_repo)
        assert result.compileall_ok is False
        assert "compileall" in result.failed_steps

    def test_no_tests_dir_skips_pytest(self, valid_repo: Path):
        result = run_validation(valid_repo)
        assert result.pytest_ok is True
        assert "skipped" in result.pytest_summary

    def test_passing_tests_succeeds(self, repo_with_passing_tests: Path):
        result = run_validation(repo_with_passing_tests)
        assert result.pytest_ok is True

    def test_failing_tests_fails(self, repo_with_failing_tests: Path):
        result = run_validation(repo_with_failing_tests)
        assert result.pytest_ok is False
        assert "pytest" in result.failed_steps

    def test_result_is_frozen(self, valid_repo: Path):
        result = run_validation(valid_repo)
        with pytest.raises((AttributeError, TypeError)):
            result.compileall_ok = False  # type: ignore[misc]

    def test_failed_steps_is_tuple(self, valid_repo: Path):
        result = run_validation(valid_repo)
        assert isinstance(result.failed_steps, tuple)


# ---------------------------------------------------------------------------
# ApplyAudit
# ---------------------------------------------------------------------------

def _sample_audit(succeeded: bool = True) -> ApplyAudit:
    move = ApprovedMove(kind="symbol", source_file="src/a.py", symbol="Foo", target_file="src/b.py")
    rope_result = RopeChangeResult(changed_files=("src/a.py", "src/b.py"))
    validation = ValidationResult(
        compileall_ok=succeeded, pytest_ok=succeeded, pytest_summary="2 passed",
        failed_steps=() if succeeded else ("pytest",),
    )
    return ApplyAudit(
        plan_hash="abc123",
        sandbox_branch="refactor-1",
        moves_applied=(move,),
        rope_results=(rope_result,),
        validation=validation,
        succeeded=succeeded,
    )


class TestApplyAudit:
    def test_is_frozen(self):
        audit = _sample_audit()
        with pytest.raises((AttributeError, TypeError)):
            audit.succeeded = False  # type: ignore[misc]

    def test_to_json_is_valid(self):
        audit = _sample_audit()
        parsed = json.loads(audit.to_json())
        assert parsed["plan_hash"] == "abc123"
        assert parsed["sandbox_branch"] == "refactor-1"
        assert parsed["succeeded"] is True
        assert len(parsed["moves_applied"]) == 1
        assert len(parsed["rope_results"]) == 1

    def test_to_json_includes_validation(self):
        audit = _sample_audit()
        parsed = json.loads(audit.to_json())
        assert parsed["validation"]["compileall_ok"] is True
        assert parsed["validation"]["pytest_ok"] is True

    def test_save_writes_to_disk(self, tmp_path: Path):
        audit = _sample_audit()
        path = audit.save(tmp_path / "audits")
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["succeeded"] is True

    def test_timestamp_auto_populated(self):
        audit = _sample_audit()
        assert audit.timestamp != ""
        assert "T" in audit.timestamp  # ISO 8601 format
