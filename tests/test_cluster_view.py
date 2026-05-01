"""Tests for cluster_view.py — file-level community projection + graphify passthroughs."""

from pathlib import Path

import pytest

from refactor_plan.cluster_view import GraphView, build_view

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_PKG = Path(__file__).parent / "fixtures" / "messy_repo" / "messy_pkg"


def test_build_view_returns_graphview_with_at_least_two_clusters():
    view = build_view(FIXTURE_GRAPH)
    assert isinstance(view, GraphView)
    assert len(view.file_clusters) >= 2


def test_all_messy_pkg_py_files_are_in_some_cluster():
    view = build_view(FIXTURE_GRAPH)
    py_files = {str(p) for p in FIXTURE_PKG.glob("*.py")}
    # Normalise to relative paths as stored in the graph
    all_cluster_files = {f for fc in view.file_clusters for f in fc.files}
    for py_file in py_files:
        # graph uses repo-relative paths; match by filename suffix
        assert any(
            cf.endswith(Path(py_file).name) for cf in all_cluster_files
        ), f"{py_file} not found in any cluster"


def test_at_least_two_misplaced_symbols_from_god_py():
    view = build_view(FIXTURE_GRAPH)
    god_misplaced = [
        m for m in view.misplaced_symbols if m.host_file.endswith("god.py")
    ]
    assert len(god_misplaced) >= 2, f"Expected >= 2 misplaced in god.py, got {god_misplaced}"

    labels = {m.label for m in god_misplaced}
    assert "vec_from_pair()" in labels
    assert "read_first_line()" in labels

    for m in god_misplaced:
        assert m.target_community != m.host_community


def test_build_view_is_deterministic():
    view_a = build_view(FIXTURE_GRAPH)
    view_b = build_view(FIXTURE_GRAPH)
    assert view_a.model_dump() == view_b.model_dump()


def test_passthroughs_are_non_empty():
    view = build_view(FIXTURE_GRAPH)
    assert isinstance(view.god_nodes, list) and len(view.god_nodes) > 0
    assert isinstance(view.surprising_connections, list) and len(view.surprising_connections) > 0
    assert isinstance(view.suggested_questions, list) and len(view.suggested_questions) > 0


def test_community_cohesion_has_all_five_community_ids():
    view = build_view(FIXTURE_GRAPH)
    assert set(view.community_cohesion.keys()) == {0, 1, 2, 3, 4}
