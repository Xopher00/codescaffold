"""Tests for reporter.py — composing graphify report + delta header into STRUCTURE_REPORT.md."""

from pathlib import Path

import pytest

from refactor_plan.entropy.cleaner import DeadCodeReport, DeadSymbol
from refactor_plan.interface.cluster_view import build_view
from refactor_plan.planning.planner import plan
from refactor_plan.reporting.reporter import render_apply_report, render_dead_code_report_md, render_dry_run_report

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
    return plan(view, FIXTURE_REPO, FIXTURE_GRAPH)


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

    assert content1 == content2  # dry-run determinism


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

    assert content1 == content2  # apply determinism


# ---------------------------------------------------------------------------
# A6 — Apply report shows Before and After sections for god_nodes etc.
# ---------------------------------------------------------------------------


def test_render_apply_report_contains_before_after_markers(refactor_plan, view, tmp_path):
    """A6: Apply report must contain both ### Before and ### After within god_nodes section."""
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
    assert "### Before" in content, "Apply report missing '### Before' marker"
    assert "### After" in content, "Apply report missing '### After' marker"


def test_render_apply_report_before_after_with_differing_views(refactor_plan, view, tmp_path):
    """A6: When pre_view and post_view differ, both Before and After sections appear."""
    from refactor_plan.interface.cluster_view import GraphView

    # Build a synthetic post_view with different god_nodes
    post_view = GraphView(
        file_clusters=view.file_clusters,
        misplaced_symbols=view.misplaced_symbols,
        god_nodes=[{"label": "synthetic_god", "edges": 999}],
        surprising_connections=view.surprising_connections,
        suggested_questions=view.suggested_questions,
        community_cohesion=view.community_cohesion,
    )

    output_path = tmp_path / "report.md"
    render_apply_report(
        refactor_plan,
        view,
        post_view,
        FIXTURE_GRAPH,
        FIXTURE_GRAPH,
        {},
        None,
        output_path,
        repo_root=FIXTURE_REPO,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "### Before" in content, "Apply report missing '### Before' in god_nodes delta"
    assert "### After" in content, "Apply report missing '### After' in god_nodes delta"
    # The synthetic post_view god node label should appear in the After section
    assert "synthetic_god" in content, (
        "After section should contain post_view god node 'synthetic_god'"
    )


# ---------------------------------------------------------------------------
# Dead code report rendering
# ---------------------------------------------------------------------------


def _make_dead_sym(
    label: str = "dead_func()",
    approved: bool = False,
) -> DeadSymbol:
    return DeadSymbol(
        node_id="sym_x",
        label=label,
        source_file="pkg/mod.py",
        source_location="L5",
        rationale="0 incoming + not exported, isolated_nodes signal present. Degree=0.",
        edge_context="0 EXTRACTED incoming, 2 INFERRED edges excluded",
        approved=approved,
    )


def test_render_dead_code_report_md_contains_header():
    """Rendered markdown must contain the DEAD_CODE_REPORT heading."""
    report = DeadCodeReport(symbols=[_make_dead_sym()])
    md = render_dead_code_report_md(report)
    assert "# DEAD_CODE_REPORT" in md


def test_render_dead_code_report_md_table_rows():
    """Each DeadSymbol produces a table row with label, source file, approved marker."""
    sym = _make_dead_sym(label="my_func()", approved=False)
    report = DeadCodeReport(symbols=[sym])
    md = render_dead_code_report_md(report)

    assert "my_func()" in md
    assert "pkg/mod.py" in md
    assert "[ ]" in md  # unapproved
    assert "EXTRACTED incoming" in md


def test_render_dead_code_report_md_approved_marker():
    """Approved=True → [x]; Approved=False → [ ]."""
    approved = _make_dead_sym(label="good_func()", approved=True)
    unapproved = _make_dead_sym(label="bad_func()", approved=False)
    report = DeadCodeReport(symbols=[approved, unapproved])
    md = render_dead_code_report_md(report)

    assert "[x]" in md
    assert "[ ]" in md
