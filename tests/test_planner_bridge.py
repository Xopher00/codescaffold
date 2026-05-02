from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.planning.planner import _detect_source_root, plan as build_plan
from refactor_plan.interface.cluster_view import ClusterView
import networkx as nx


# ---------------------------------------------------------------------------
# _detect_source_root
# ---------------------------------------------------------------------------

def test_detect_source_root_finds_src(tmp_path: Path) -> None:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    files = [str(src / "a.py"), str(src / "b.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path / "src"


def test_detect_source_root_ignores_test_paths(tmp_path: Path) -> None:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    tests = tmp_path / "tests"
    tests.mkdir()
    # tests/ files come first — should be filtered out
    files = [str(tests / "test_a.py"), str(src / "a.py"), str(src / "b.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path / "src"


def test_detect_source_root_falls_back_to_repo_root(tmp_path: Path) -> None:
    files = [str(tmp_path / "a.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path


# ---------------------------------------------------------------------------
# planner — single-file community
# ---------------------------------------------------------------------------

def _make_view(file_communities: dict[int, list[str]]) -> ClusterView:
    return ClusterView(file_communities=file_communities, G=nx.Graph())


def test_plan_single_file_community_proposes_move(tmp_path: Path) -> None:
    """Single-file communities must produce a file move, not be silently skipped."""
    files = {0: [str(tmp_path / "a.py")], 1: [str(tmp_path / "b.py")]}
    view = _make_view(files)
    graph_json = tmp_path / "graph.json"
    graph_json.write_text("{}")
    result = build_plan(view, tmp_path, graph_json)
    # Each single-file community should have a proposed_package
    for cluster in result.clusters:
        assert cluster.proposed_package is not None, (
            f"community {cluster.community_id} has no proposed_package"
        )


def test_plan_multi_file_same_dir_skips_move(tmp_path: Path) -> None:
    """Multi-file communities already in one dir should not be moved."""
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    files = {0: [str(pkg / "a.py"), str(pkg / "b.py")]}
    view = _make_view(files)
    graph_json = tmp_path / "graph.json"
    graph_json.write_text("{}")
    result = build_plan(view, tmp_path, graph_json)
    assert result.file_moves == []
    assert result.clusters[0].proposed_package is None


# ---------------------------------------------------------------------------
# ensure_graph — stale cache warning
# ---------------------------------------------------------------------------

def test_ensure_graph_warns_on_stale_cache(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    graph_out = tmp_path / "graphify-out" / "graph.json"
    graph_out.parent.mkdir(parents=True)
    graph_out.write_text('{"nodes":[],"links":[],"directed":true,"multigraph":false,"graph":{}}')

    # Write a .py file newer than the graph
    time.sleep(0.01)
    (tmp_path / "new_file.py").write_text("x = 1\n")

    with caplog.at_level(logging.WARNING, logger="refactor_plan.interface.graph_bridge"):
        result = ensure_graph(tmp_path)

    assert result == graph_out
    assert any("stale" in r.message for r in caplog.records)


def test_ensure_graph_no_warning_when_fresh(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "existing.py").write_text("x = 1\n")
    time.sleep(0.01)

    graph_out = tmp_path / "graphify-out" / "graph.json"
    graph_out.parent.mkdir(parents=True)
    graph_out.write_text('{"nodes":[],"links":[],"directed":true,"multigraph":false,"graph":{}}')

    with caplog.at_level(logging.WARNING, logger="refactor_plan.interface.graph_bridge"):
        ensure_graph(tmp_path)

    assert not any("stale" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# validator — runs all commands even after failure
# ---------------------------------------------------------------------------

def test_validate_runs_all_commands_after_failure(tmp_path: Path) -> None:
    from refactor_plan.validation.validator import validate

    commands = ["false", "true"]  # first fails, second succeeds
    report = validate(tmp_path, commands=commands)

    assert report.passed is False
    assert len(report.commands) == 2
    assert report.commands[0].exit_code != 0
    assert report.commands[1].exit_code == 0


def test_validate_passed_when_all_succeed(tmp_path: Path) -> None:
    from refactor_plan.validation.validator import validate

    report = validate(tmp_path, commands=["true", "true"])
    assert report.passed is True
    assert len(report.commands) == 2


# ---------------------------------------------------------------------------
# apply_plan — import rewrite failures surface in result.skipped
# ---------------------------------------------------------------------------

def test_apply_plan_import_rewrite_failure_in_skipped(
    messy_repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from refactor_plan.applicator.apply import apply_plan

    broken = messy_repo / "src" / "messy_pkg" / "broken.py"
    broken.write_text("this is @@@@ not valid python\n")

    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    dest = messy_repo / "src" / "messy_pkg" / "extracted.py"
    plan = {
        "file_moves": [],
        "symbol_moves": [{"source": str(src), "dest": str(dest), "symbol": "helper"}],
    }
    out_dir = messy_repo / ".refactor_plan"
    with caplog.at_level(logging.WARNING, logger="refactor_plan.applicator.apply"):
        result = apply_plan(plan, messy_repo, out_dir, dry_run=False)

    assert any("import rewrite failed" in r.message for r in caplog.records)
    assert any(e.category == "import_rewrite" for e in result.skipped)
