from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from refactor_plan.cli import app
from refactor_plan.planner import RefactorPlan

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    fixture_src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(fixture_src, dst)
    return dst


def test_analyze_writes_plan_and_report(repo: Path) -> None:
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"
    report_path = repo / ".refactor_plan" / "STRUCTURE_REPORT.md"
    plan_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)

    result = runner.invoke(app, ["analyze", str(repo)])

    assert result.exit_code == 0
    assert "wrote refactor_plan.json" in result.stdout
    assert plan_path.exists()
    assert report_path.exists()
    plan = RefactorPlan.model_validate_json(plan_path.read_text())
    assert len(plan.file_moves) >= 9
    assert len(plan.symbol_moves) >= 5
    assert "STRUCTURE_REPORT" in report_path.read_text()


def test_analyze_generates_graph_when_missing(repo: Path) -> None:
    """When graph.json is absent, ensure_graph auto-generates it before analysis."""
    graph_path = repo / ".refactor_plan" / "graph.json"
    graph_path.unlink()
    assert not graph_path.exists()

    result = runner.invoke(app, ["analyze", str(repo)])

    assert result.exit_code == 0, result.output
    assert graph_path.exists()
    assert "wrote refactor_plan.json" in result.stdout


def test_help_lists_only_wave_c_subcommands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ["analyze", "apply", "split", "clean", "name"]:
        assert command in result.stdout
    for old_command in ["extract", "plan", "report", "run"]:
        assert f"│ {old_command} " not in result.stdout
