"""Tests for the dry-run CLI (extract, plan, report, run commands)."""

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
    """Copy the fixture repo to a temp directory for testing."""
    fixture_src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(fixture_src, dst)
    return dst


def test_extract_skips_when_graph_json_exists(repo: Path) -> None:
    """If graph.json exists and --force is not set, extract should skip."""
    # Fixture already has graph.json
    assert (repo / ".refactor_plan" / "graph.json").exists()

    result = runner.invoke(app, ["extract", str(repo)])

    assert result.exit_code == 0
    assert "already present, skipping" in result.stdout


def test_extract_with_force_runs_graphify_or_skipped_if_unavailable(
    repo: Path,
) -> None:
    """If graphify is available, --force should regenerate. If not, skip."""
    if shutil.which("graphify") is None:
        pytest.skip("graphify CLI not available")

    # Remove graph.json to force regeneration
    graph_path = repo / ".refactor_plan" / "graph.json"
    graph_path.unlink()

    assert not graph_path.exists()

    result = runner.invoke(app, ["extract", str(repo), "--force"])

    assert result.exit_code == 0
    assert "extracted graph" in result.stdout
    assert graph_path.exists()

    # New graph.json should exist (content may differ)
    new_content = graph_path.read_text()
    assert len(new_content) > 0


def test_extract_missing_graph_json_no_graphify(repo: Path) -> None:
    """If graph.json is missing and graphify is not available, extract should fail."""
    if shutil.which("graphify") is not None:
        pytest.skip("graphify CLI is available; this test only runs when unavailable")

    # Remove graph.json
    graph_path = repo / ".refactor_plan" / "graph.json"
    graph_path.unlink()

    result = runner.invoke(app, ["extract", str(repo)])

    assert result.exit_code == 1


def test_plan_writes_refactor_plan_json(repo: Path) -> None:
    """Running plan should write a valid refactor_plan.json."""
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"

    # Ensure plan doesn't exist yet
    if plan_path.exists():
        plan_path.unlink()

    result = runner.invoke(app, ["plan", str(repo)])

    assert result.exit_code == 0
    assert "wrote refactor_plan.json" in result.stdout
    assert plan_path.exists()

    # Validate the JSON structure
    plan = RefactorPlan.model_validate_json(plan_path.read_text())
    assert len(plan.file_moves) >= 9
    assert len(plan.symbol_moves) >= 5  # A1: methods filtered (.echo()), so 5 non-method moves


def test_plan_fails_without_graph_json(repo: Path) -> None:
    """If graph.json is missing, plan should fail with helpful error."""
    graph_path = repo / ".refactor_plan" / "graph.json"
    graph_path.unlink()

    result = runner.invoke(app, ["plan", str(repo)])

    assert result.exit_code == 1
    assert "missing" in result.stdout or "missing" in result.stderr


def test_report_writes_structure_report_md(repo: Path) -> None:
    """Running report should write a non-empty STRUCTURE_REPORT.md."""
    report_path = repo / ".refactor_plan" / "STRUCTURE_REPORT.md"

    # Ensure plan exists (prerequisite for report)
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"
    if not plan_path.exists():
        runner.invoke(app, ["plan", str(repo)])

    # Ensure report doesn't exist yet
    if report_path.exists():
        report_path.unlink()

    result = runner.invoke(app, ["report", str(repo)])

    assert result.exit_code == 0
    assert "wrote STRUCTURE_REPORT.md" in result.stdout
    assert report_path.exists()

    content = report_path.read_text()
    assert len(content) > 0
    assert "STRUCTURE_REPORT" in content


def test_report_fails_without_graph_json(repo: Path) -> None:
    """If graph.json is missing, report should fail."""
    graph_path = repo / ".refactor_plan" / "graph.json"
    graph_path.unlink()

    result = runner.invoke(app, ["report", str(repo)])

    assert result.exit_code == 1


def test_report_fails_without_plan(repo: Path) -> None:
    """If refactor_plan.json is missing, report should fail."""
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"
    if plan_path.exists():
        plan_path.unlink()

    result = runner.invoke(app, ["report", str(repo)])

    assert result.exit_code == 1
    assert "missing" in result.stdout or "missing" in result.stderr


def test_run_chains_all_stages(repo: Path) -> None:
    """Running 'run' should produce all three artifacts."""
    graph_path = repo / ".refactor_plan" / "graph.json"
    plan_path = repo / ".refactor_plan" / "refactor_plan.json"
    report_path = repo / ".refactor_plan" / "STRUCTURE_REPORT.md"

    # Clean up plan and report (keep graph.json)
    if plan_path.exists():
        plan_path.unlink()
    if report_path.exists():
        report_path.unlink()

    result = runner.invoke(app, ["run", str(repo)])

    assert result.exit_code == 0

    # All three artifacts should exist
    assert graph_path.exists(), "graph.json should exist after run"
    assert plan_path.exists(), "refactor_plan.json should exist after run"
    assert report_path.exists(), "STRUCTURE_REPORT.md should exist after run"

    # Validate they are not empty
    assert len(graph_path.read_text()) > 0
    assert len(plan_path.read_text()) > 0
    assert len(report_path.read_text()) > 0
