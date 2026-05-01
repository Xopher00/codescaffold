"""Tests for reporter.py — composing graphify report + delta header into STRUCTURE_REPORT.md."""

from pathlib import Path

import pytest

from refactor_plan.cluster_view import build_view
from refactor_plan.planner import plan
from refactor_plan.reporter import render_dry_run_report, render_apply_report

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"


@pytest.fixture(scope="module")
def view():
    return build_view(FIXTURE_GRAPH)


@pytest.fixture(scope="module")
def refactor_plan(view):
    return plan(view, FIXTURE_REPO)


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------


def test_render_dry_run_report_produces_non_empty_output(refactor_plan, view, tmp_path):
    """Test that dry-run report generates non-empty markdown output."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert len(content) > 0


def test_render_dry_run_report_contains_title(refactor_plan, view, tmp_path):
    """Test that dry-run report contains main title."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "# STRUCTURE_REPORT" in content


def test_render_dry_run_report_contains_summary_section(refactor_plan, view, tmp_path):
    """Test that dry-run report contains summary section."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## Summary" in content


def test_render_dry_run_report_contains_clusters_section(refactor_plan, view, tmp_path):
    """Test that dry-run report contains clusters table."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## Clusters" in content


def test_render_dry_run_report_contains_file_moves_section(refactor_plan, view, tmp_path):
    """Test that dry-run report contains file moves table."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## File moves" in content


def test_render_dry_run_report_contains_symbol_moves_section(refactor_plan, view, tmp_path):
    """Test that dry-run report contains symbol moves table."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## Symbol moves" in content


def test_render_dry_run_report_contains_expected_symbols(refactor_plan, view, tmp_path):
    """Test that dry-run report references expected misplaced symbols."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "vec_from_pair()" in content
    assert "read_first_line()" in content


def test_render_dry_run_report_contains_pkg_references(refactor_plan, view, tmp_path):
    """Test that dry-run report contains pkg_001, etc."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "pkg_001" in content


def test_render_dry_run_report_contains_god_nodes_section(refactor_plan, view, tmp_path):
    """Test that dry-run report contains god nodes section."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## God nodes" in content


def test_render_dry_run_report_contains_surprising_connections_section(
    refactor_plan, view, tmp_path
):
    """Test that dry-run report contains surprising connections section."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## Surprising connections" in content


def test_render_dry_run_report_contains_suggested_questions_section(
    refactor_plan, view, tmp_path
):
    """Test that dry-run report contains suggested questions section."""
    output_path = tmp_path / "report.md"
    render_dry_run_report(refactor_plan, view, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## Suggested questions" in content


def test_render_dry_run_report_is_deterministic(refactor_plan, view, tmp_path):
    """Test that dry-run reports are deterministic (same output on re-render)."""
    path1 = tmp_path / "report1.md"
    path2 = tmp_path / "report2.md"

    render_dry_run_report(refactor_plan, view, path1)
    render_dry_run_report(refactor_plan, view, path2)

    content1 = path1.read_text(encoding="utf-8")
    content2 = path2.read_text(encoding="utf-8")

    assert content1 == content2


# ---------------------------------------------------------------------------
# Apply tests
# ---------------------------------------------------------------------------


def test_render_apply_report_produces_non_empty_output(refactor_plan, view, tmp_path):
    """Test that apply report generates non-empty markdown output."""
    output_path = tmp_path / "report.md"
    manifest = {
        "file_moves_applied": 5,
        "symbol_moves_applied": 2,
        "shims_created": 0,
        "imports_organized": 7,
    }
    validation = {
        "compileall": "OK",
        "pytest": "OK",
    }

    # For degenerate case: use same view/graph for pre and post
    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert len(content) > 0


def test_render_apply_report_contains_title(refactor_plan, view, tmp_path):
    """Test that apply report contains main title."""
    output_path = tmp_path / "report.md"
    manifest = {}
    validation = {}

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "# STRUCTURE_REPORT" in content


def test_render_apply_report_contains_pre_graphify_section(refactor_plan, view, tmp_path):
    """Test that apply report contains pre-apply graphify report section."""
    output_path = tmp_path / "report.md"
    manifest = {}
    validation = {}

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "## Pre-apply graphify report" in content


def test_render_apply_report_contains_post_graphify_section(refactor_plan, view, tmp_path):
    """Test that apply report contains post-apply graphify report section."""
    output_path = tmp_path / "report.md"
    manifest = {}
    validation = {}

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "## Post-apply graphify report" in content


def test_render_apply_report_contains_graph_diff_section(refactor_plan, view, tmp_path):
    """Test that apply report contains graph diff section."""
    output_path = tmp_path / "report.md"
    manifest = {}
    validation = {}

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "## Graph diff" in content


def test_render_apply_report_contains_validation_results(refactor_plan, view, tmp_path):
    """Test that apply report contains validation results section."""
    output_path = tmp_path / "report.md"
    manifest = {}
    validation = {
        "compileall": "OK",
        "pytest": "FAILED",
    }

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "## Validation results" in content
    assert "compileall" in content
    assert "pytest" in content


def test_render_apply_report_contains_manifest_section(refactor_plan, view, tmp_path):
    """Test that apply report contains manifest section."""
    output_path = tmp_path / "report.md"
    manifest = {
        "file_moves_applied": 5,
        "symbol_moves_applied": 2,
        "shims_created": 1,
        "imports_organized": 7,
    }
    validation = {}

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "## Manifest" in content
    assert "file_moves_applied" in content or "File moves applied" in content


def test_render_apply_report_with_none_validation(refactor_plan, view, tmp_path):
    """Test that apply report handles None validation gracefully."""
    output_path = tmp_path / "report.md"
    manifest = {}

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        None,  # validation is None
        output_path,
        repo_root=FIXTURE_REPO,
    )

    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert len(content) > 0


def test_render_apply_report_is_deterministic(refactor_plan, view, tmp_path):
    """Test that apply reports are deterministic (same output on re-render)."""
    path1 = tmp_path / "report1.md"
    path2 = tmp_path / "report2.md"

    manifest = {
        "file_moves_applied": 5,
        "symbol_moves_applied": 2,
        "shims_created": 0,
        "imports_organized": 7,
    }
    validation = {
        "compileall": "OK",
    }

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        path1,
        repo_root=FIXTURE_REPO,
    )

    render_apply_report(
        refactor_plan,
        view,
        view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        manifest,
        validation,
        path2,
        repo_root=FIXTURE_REPO,
    )

    content1 = path1.read_text(encoding="utf-8")
    content2 = path2.read_text(encoding="utf-8")

    assert content1 == content2
