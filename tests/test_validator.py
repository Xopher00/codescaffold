"""Tests for src/refactor_plan/validator.py (T9)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from refactor_plan.applicator.rope_runner import Escalation
from refactor_plan.validator import ValidationReport, validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str) -> Path:
    cfg = tmp_path / "refactor.toml"
    cfg.write_text(content, encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_config_passes(tmp_path: Path) -> None:
    """No refactor.toml → trivially passed, no commands run."""
    report = validate(tmp_path, applied_count=0, write_report=False)
    assert report.passed is True
    assert report.commands == []
    assert report.rolled_back is False


def test_empty_commands_passes(tmp_path: Path) -> None:
    """Config with commands=[] → passed, no commands."""
    _write_toml(tmp_path, "[validate]\ncommands = []\n")
    report = validate(tmp_path, applied_count=0, write_report=False)
    assert report.passed is True
    assert report.commands == []


def test_all_passing_commands(tmp_path: Path) -> None:
    """All commands exit 0 → passed, results recorded."""
    _write_toml(tmp_path, '[validate]\ncommands = ["python -c \'pass\'", "true"]\n')
    report = validate(tmp_path, applied_count=0, write_report=False)
    assert report.passed is True
    assert len(report.commands) == 2
    assert all(r.exit_code == 0 for r in report.commands)
    assert report.rolled_back is False


def test_failing_command_sets_passed_false(tmp_path: Path) -> None:
    """A failing command marks the report as not passed."""
    _write_toml(tmp_path, '[validate]\ncommands = ["false"]\n')
    with patch("refactor_plan.validator.rollback"):
        report = validate(tmp_path, applied_count=0, write_report=False)
    assert report.passed is False
    assert report.commands[0].exit_code != 0


def test_fail_fast_stops_on_first_failure(tmp_path: Path) -> None:
    """fail_fast=true → only the first failing command is recorded."""
    _write_toml(
        tmp_path,
        '[validate]\nfail_fast = true\ncommands = ["false", "echo should-not-run"]\n',
    )
    with patch("refactor_plan.validator.rollback"):
        report = validate(tmp_path, applied_count=0, write_report=False)
    assert report.passed is False
    assert len(report.commands) == 1
    assert report.commands[0].command == "false"


def test_no_fail_fast_runs_all(tmp_path: Path) -> None:
    """fail_fast=false → all commands run even after a failure."""
    _write_toml(
        tmp_path,
        '[validate]\nfail_fast = false\ncommands = ["false", "echo should-run"]\n',
    )
    with patch("refactor_plan.validator.rollback"):
        report = validate(tmp_path, applied_count=0, write_report=False)
    assert report.passed is False
    assert len(report.commands) == 2
    cmds = [r.command for r in report.commands]
    assert "echo should-run" in cmds


def test_lint_imports_auto_appended_when_importlinter_exists(tmp_path: Path) -> None:
    """.importlinter present → lint-imports appended automatically."""
    (tmp_path / ".importlinter").write_text("", encoding="utf-8")
    _write_toml(tmp_path, "[validate]\ncommands = []\n")
    with patch("refactor_plan.validator.rollback"):
        report = validate(tmp_path, applied_count=0, write_report=False)
    cmd_strings = [r.command for r in report.commands]
    assert "lint-imports" in cmd_strings


def test_lint_imports_not_duplicated(tmp_path: Path) -> None:
    """lint-imports already in config + .importlinter present → only one entry."""
    (tmp_path / ".importlinter").write_text("", encoding="utf-8")
    _write_toml(tmp_path, '[validate]\ncommands = ["lint-imports"]\n')
    with patch("refactor_plan.validator.rollback"):
        report = validate(tmp_path, applied_count=0, write_report=False)
    cmd_strings = [r.command for r in report.commands]
    assert cmd_strings.count("lint-imports") == 1


def test_rollback_invoked_on_failure(tmp_path: Path) -> None:
    """On failure, rollback is called once with (repo_root, applied_count)."""
    _write_toml(tmp_path, '[validate]\ncommands = ["false"]\n')
    with patch("refactor_plan.validator.rollback") as mock_rollback:
        report = validate(tmp_path, applied_count=3, write_report=False)
    assert report.passed is False
    assert report.rolled_back is True
    mock_rollback.assert_called_once_with(tmp_path, 3)


def test_cleanup_paths_deleted_on_failure(tmp_path: Path) -> None:
    """cleanup_paths are deleted on failure; their relative names recorded."""
    shim_file = tmp_path / "shim.py"
    shim_file.write_text("# shim", encoding="utf-8")
    other_file = tmp_path / "extra.py"
    other_file.write_text("# extra", encoding="utf-8")

    _write_toml(tmp_path, '[validate]\ncommands = ["false"]\n')
    with patch("refactor_plan.validator.rollback"):
        report = validate(
            tmp_path,
            applied_count=0,
            cleanup_paths=[shim_file, other_file],
            write_report=False,
        )

    assert report.passed is False
    assert not shim_file.exists()
    assert not other_file.exists()
    assert "shim.py" in report.cleanup_deleted
    assert "extra.py" in report.cleanup_deleted


def test_cleanup_paths_not_deleted_on_success(tmp_path: Path) -> None:
    """cleanup_paths are NOT touched when validation passes."""
    shim_file = tmp_path / "shim.py"
    shim_file.write_text("# shim", encoding="utf-8")

    _write_toml(tmp_path, '[validate]\ncommands = ["true"]\n')
    report = validate(
        tmp_path,
        applied_count=0,
        cleanup_paths=[shim_file],
        write_report=False,
    )

    assert report.passed is True
    assert shim_file.exists()
    assert report.cleanup_deleted == []


def test_validation_report_json_written(tmp_path: Path) -> None:
    """write_report=True → JSON file exists and round-trips."""
    _write_toml(tmp_path, '[validate]\ncommands = ["true"]\n')
    report = validate(tmp_path, applied_count=0, write_report=True)

    report_path = tmp_path / ".refactor_plan" / "validation_report.json"
    assert report_path.exists()
    roundtripped = ValidationReport.model_validate_json(report_path.read_text())
    assert roundtripped.passed == report.passed
    assert len(roundtripped.commands) == len(report.commands)


def test_escalations_passed_through(tmp_path: Path) -> None:
    """Escalations supplied at call-time appear verbatim in the report."""
    esc = Escalation(kind="no_referent", symbol_id="x", detail="y")
    _write_toml(tmp_path, "[validate]\ncommands = []\n")
    report = validate(tmp_path, applied_count=0, escalations=[esc], write_report=False)
    assert len(report.escalations) == 1
    assert report.escalations[0].kind == "no_referent"
    assert report.escalations[0].symbol_id == "x"
    assert report.escalations[0].detail == "y"
