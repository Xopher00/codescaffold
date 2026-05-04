from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.layout import (
    _detect_cluster_root,
    _detect_source_root,
    _is_test_file,
    detect_layout,
)
from refactor_plan.planning.planner import plan as build_plan
from refactor_plan.interface.cluster_view import ClusterView
import networkx as nx
from refactor_plan.execution.apply import _ensure_package_inits, apply_plan


# ---------------------------------------------------------------------------
# _is_test_file
# ---------------------------------------------------------------------------

def test_is_test_file_test_dir() -> None:
    assert _is_test_file(Path("/repo/tests/test_foo.py")) is True


def test_is_test_file_test_prefix() -> None:
    assert _is_test_file(Path("/repo/src/pkg/test_utils.py")) is True


def test_is_test_file_fixture_dir() -> None:
    assert _is_test_file(Path("/repo/tests/fixtures/sample.py")) is True


def test_is_test_file_conftest() -> None:
    assert _is_test_file(Path("/repo/conftest.py")) is True


def test_is_test_file_source_module() -> None:
    assert _is_test_file(Path("/repo/src/mypackage/module.py")) is False


# ---------------------------------------------------------------------------
# _detect_source_root
# ---------------------------------------------------------------------------

def test_detect_source_root_prefers_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )
    (tmp_path / "src").mkdir()
    files = [str(tmp_path / "src" / "mypkg" / "a.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path / "src"


def test_detect_source_root_finds_src_heuristic(tmp_path: Path) -> None:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    files = [str(src / "a.py"), str(src / "b.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path / "src"


def test_detect_source_root_ignores_test_paths(tmp_path: Path) -> None:
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    tests = tmp_path / "tests"
    tests.mkdir()
    files = [str(tests / "test_a.py"), str(src / "a.py"), str(src / "b.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path / "src"


def test_detect_source_root_falls_back_to_repo_root(tmp_path: Path) -> None:
    files = [str(tmp_path / "a.py")]
    assert _detect_source_root(tmp_path, files) == tmp_path


# ---------------------------------------------------------------------------
# _detect_cluster_root
# ---------------------------------------------------------------------------

def test_detect_cluster_root_finds_package_under_src(tmp_path: Path) -> None:
    """For a src-layout project, cluster root is the top-level package, not src/."""
    pkg = tmp_path / "src" / "mypackage"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "module_a.py").write_text("")
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "thing.py").write_text("")

    src_root = tmp_path / "src"
    files = [str(pkg / "module_a.py"), str(sub / "thing.py")]
    result = _detect_cluster_root(tmp_path, files, src_root)
    assert result == pkg  # clusters go inside mypackage/, not alongside it


def test_detect_cluster_root_falls_back_to_src_for_multi_package(tmp_path: Path) -> None:
    """When files span multiple top-level packages, clusters go at the src/ level."""
    pkg_a = tmp_path / "src" / "pkg_a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "__init__.py").write_text("")
    pkg_b = tmp_path / "src" / "pkg_b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "__init__.py").write_text("")

    src_root = tmp_path / "src"
    files = [str(pkg_a / "a.py"), str(pkg_b / "b.py")]
    result = _detect_cluster_root(tmp_path, files, src_root)
    assert result == src_root  # no common package, fall back to src/


def test_detect_cluster_root_flat_layout(tmp_path: Path) -> None:
    """Flat layout: cluster root is the single top-level package dir."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "module.py").write_text("")

    src_root = tmp_path  # no src/ dir
    files = [str(pkg / "module.py")]
    result = _detect_cluster_root(tmp_path, files, src_root)
    assert result == pkg


def test_detect_cluster_root_ignores_test_files(tmp_path: Path) -> None:
    """Test files must not influence the cluster root detection."""
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    tests = tmp_path / "tests"
    tests.mkdir()

    src_root = tmp_path / "src"
    files = [str(pkg / "module.py"), str(tests / "test_module.py")]
    result = _detect_cluster_root(tmp_path, files, src_root)
    assert result == pkg  # tests/ didn't pull common ancestor up to tmp_path


# ---------------------------------------------------------------------------
# planner — helpers
# ---------------------------------------------------------------------------

def _make_view(file_communities: dict[int, list[str]]) -> ClusterView:
    return ClusterView(file_communities=file_communities, G=nx.Graph())


# ---------------------------------------------------------------------------
# planner — noise filtering
# ---------------------------------------------------------------------------

def test_plan_empty_community_produces_no_cluster(tmp_path: Path) -> None:
    """Communities with no source files after filtering must be skipped entirely."""
    view = _make_view({0: []})
    result = build_plan(view, tmp_path, tmp_path / "graph.json")
    assert result.clusters == []
    assert result.file_moves == []


def test_plan_init_only_community_produces_no_cluster(tmp_path: Path) -> None:
    """Communities whose only file is __init__.py must be skipped."""
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    view = _make_view({0: [str(pkg / "__init__.py")]})
    result = build_plan(view, tmp_path, tmp_path / "graph.json")
    assert result.clusters == []


def test_plan_single_file_community_no_move(tmp_path: Path) -> None:
    """Single-file communities are recorded but must not generate a file move."""
    (tmp_path / "a.py").write_text("")
    view = _make_view({0: [str(tmp_path / "a.py")]})
    result = build_plan(view, tmp_path, tmp_path / "graph.json")
    assert result.file_moves == []
    assert len(result.clusters) == 1
    assert result.clusters[0].proposed_package is None


# ---------------------------------------------------------------------------
# planner — majority co-location skip
# ---------------------------------------------------------------------------

def test_plan_majority_collocated_skips_move(tmp_path: Path) -> None:
    """When >50% of a community's files share a parent dir, no move is proposed."""
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    view = _make_view({0: [
        str(pkg / "a.py"),
        str(pkg / "b.py"),
        str(pkg / "c.py"),
        str(tmp_path / "src" / "other.py"),  # minority outsider
    ]})
    result = build_plan(view, tmp_path, tmp_path / "graph.json")
    assert result.file_moves == []
    assert result.clusters[0].proposed_package is None


# ---------------------------------------------------------------------------
# planner — file-community conflict resolution
# ---------------------------------------------------------------------------

def test_plan_file_assigned_to_first_community_only(tmp_path: Path) -> None:
    """A file appearing in multiple communities is only moved once (lowest comm id)."""
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    other_dir = tmp_path / "src" / "other"
    other_dir.mkdir(parents=True)

    shared = str(src_dir / "shared.py")
    only_in_zero = str(src_dir / "zero.py")
    only_in_one = str(other_dir / "one.py")

    # shared.py appears in both community 0 and community 1
    view = _make_view({
        0: [shared, only_in_zero],
        1: [shared, only_in_one],
    })
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    # shared.py may only appear once in all file_moves
    move_sources = [m.source for m in result.file_moves]
    assert move_sources.count(shared) <= 1, "shared.py must not be moved twice"


# ---------------------------------------------------------------------------
# planner — destination inside package
# ---------------------------------------------------------------------------

def test_plan_dest_inside_package_not_beside(tmp_path: Path) -> None:
    """Cluster destination dirs must be inside the package, not at src/ level."""
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    sub_a = pkg / "a"
    sub_a.mkdir()
    sub_b = pkg / "b"
    sub_b.mkdir()

    # Two files from different subdirs → planner should cluster them inside mypkg/
    view = _make_view({0: [str(sub_a / "x.py"), str(sub_b / "y.py")]})
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    if result.file_moves:
        for move in result.file_moves:
            dest = Path(move.dest_package)
            # Must be under mypkg/, not under src/ directly
            assert str(dest).startswith(str(pkg)), (
                f"dest {dest} is not inside package {pkg}"
            )


# ---------------------------------------------------------------------------
# planner — source_root recorded in plan
# ---------------------------------------------------------------------------

def test_plan_records_source_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )
    (tmp_path / "src").mkdir()
    view = _make_view({})
    result = build_plan(view, tmp_path, tmp_path / "graph.json")
    assert result.source_root == str(tmp_path / "src")


# ---------------------------------------------------------------------------
# _ensure_package_inits
# ---------------------------------------------------------------------------

def test_ensure_package_inits_creates_init(tmp_path: Path) -> None:

    pkg = tmp_path / "src" / "mypkg" / "new_cluster"
    pkg.mkdir(parents=True)
    boundary = tmp_path / "src"

    created = _ensure_package_inits({pkg}, boundary)
    assert (pkg / "__init__.py").exists()
    assert pkg / "__init__.py" in created


def test_ensure_package_inits_does_not_overwrite(tmp_path: Path) -> None:

    pkg = tmp_path / "src" / "mypkg" / "cluster"
    pkg.mkdir(parents=True)
    init = pkg / "__init__.py"
    init.write_text("# existing\n")
    boundary = tmp_path / "src"

    created = _ensure_package_inits({pkg}, boundary)
    assert init not in created
    assert init.read_text() == "# existing\n"


def test_ensure_package_inits_creates_ancestors(tmp_path: Path) -> None:

    # Nested: src/mypkg/outer/inner/ — outer/ needs __init__.py too
    inner = tmp_path / "src" / "mypkg" / "outer" / "inner"
    inner.mkdir(parents=True)
    # mypkg/ already has __init__.py
    (tmp_path / "src" / "mypkg").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")
    boundary = tmp_path / "src" / "mypkg"

    created = _ensure_package_inits({inner}, boundary)
    assert (inner / "__init__.py").exists()
    assert (inner.parent / "__init__.py").exists()  # outer/__init__.py
    # boundary itself must NOT get an __init__.py
    assert (boundary / "__init__.py").read_text() == ""  # pre-existing unchanged


def test_ensure_package_inits_stops_at_boundary(tmp_path: Path) -> None:

    pkg = tmp_path / "src" / "mypkg" / "cluster"
    pkg.mkdir(parents=True)
    boundary = tmp_path / "src"

    _ensure_package_inits({pkg}, boundary)
    # Must NOT create __init__.py at or above boundary
    assert not (boundary / "__init__.py").exists()
    assert not (tmp_path / "__init__.py").exists()


# ---------------------------------------------------------------------------
# validator — structural and behavioral modes
# ---------------------------------------------------------------------------

def test_validate_structural_only_runs_compileall(tmp_path: Path) -> None:
    from refactor_plan.validation.validator import validate

    report = validate(tmp_path, mode="structural")
    commands = [r.command for r in report.commands]
    assert any("compileall" in c for c in commands)
    assert not any("pytest" in c for c in commands)


def test_validate_behavioral_skipped_when_no_tests(tmp_path: Path) -> None:
    from refactor_plan.layout import ProjectLayout
    from refactor_plan.validation.validator import validate

    layout = ProjectLayout(
        source_root=tmp_path,
        cluster_root=tmp_path,
        test_roots=[],
        has_tests=False,
        root_package="",
    )
    report = validate(tmp_path, mode="behavioral", layout=layout)
    assert not any("pytest" in r.command for r in report.commands)
    assert report.passed


def test_validate_explicit_commands_override_mode(tmp_path: Path) -> None:
    from refactor_plan.validation.validator import validate

    report = validate(tmp_path, commands=["true"], mode="structural")
    assert len(report.commands) == 1
    assert report.commands[0].command == "true"


# ---------------------------------------------------------------------------
# ensure_graph — stale cache warning
# ---------------------------------------------------------------------------

def test_ensure_graph_rebuilds_when_stale(tmp_path: Path) -> None:
    from unittest.mock import MagicMock, patch

    graph_out = tmp_path / "graphify-out" / "graph.json"
    graph_out.parent.mkdir(parents=True)
    graph_out.write_text('{"nodes":[],"links":[],"directed":true,"multigraph":false,"graph":{}}')

    time.sleep(0.01)
    (tmp_path / "new_file.py").write_text("x = 1\n")

    fake_G = MagicMock()
    with patch("refactor_plan.interface.graph_bridge.collect_files", return_value=[]) as mock_collect, \
         patch("refactor_plan.interface.graph_bridge.extract", return_value={}) as mock_extract, \
         patch("refactor_plan.interface.graph_bridge.build_from_json", return_value=fake_G), \
         patch("refactor_plan.interface.graph_bridge.cluster", return_value={}), \
         patch("refactor_plan.interface.graph_bridge.to_json") as mock_to_json:
        result = ensure_graph(tmp_path)

    assert result == graph_out
    mock_collect.assert_called_once()
    mock_to_json.assert_called_once()


def test_ensure_graph_no_rebuild_when_fresh(tmp_path: Path) -> None:
    from unittest.mock import patch

    (tmp_path / "existing.py").write_text("x = 1\n")
    time.sleep(0.01)

    graph_out = tmp_path / "graphify-out" / "graph.json"
    graph_out.parent.mkdir(parents=True)
    graph_out.write_text('{"nodes":[],"links":[],"directed":true,"multigraph":false,"graph":{}}')

    with patch("refactor_plan.interface.graph_bridge.collect_files") as mock_collect:
        ensure_graph(tmp_path)

    mock_collect.assert_not_called()


# ---------------------------------------------------------------------------
# apply_plan — import rewrite failures surface in result.skipped
# ---------------------------------------------------------------------------

def test_apply_plan_import_rewrite_failure_in_skipped(
    messy_repo: Path, caplog: pytest.LogCaptureFixture
) -> None:

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


# ---------------------------------------------------------------------------
# detect_layout — pytest config readers
# ---------------------------------------------------------------------------

def test_detect_layout_reads_pytest_ini(tmp_path: Path) -> None:
    from refactor_plan.layout import detect_layout

    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = mytests\n")
    mytests = tmp_path / "mytests"
    mytests.mkdir()
    (mytests / "test_sample.py").write_text("")

    layout = detect_layout(tmp_path)
    assert tmp_path / "mytests" in layout.test_roots
    assert layout.has_tests


def test_detect_layout_reads_pyproject_pytest_options(tmp_path: Path) -> None:
    from refactor_plan.layout import detect_layout

    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["custom_tests"]\n'
    )
    custom = tmp_path / "custom_tests"
    custom.mkdir()
    (custom / "test_x.py").write_text("")

    layout = detect_layout(tmp_path)
    assert tmp_path / "custom_tests" in layout.test_roots
    assert layout.has_tests


def test_detect_layout_fallback_tests_dir(tmp_path: Path) -> None:
    from refactor_plan.layout import detect_layout

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_foo.py").write_text("")

    layout = detect_layout(tmp_path)
    assert tests in layout.test_roots
    assert layout.has_tests


def test_detect_layout_no_tests(tmp_path: Path) -> None:
    from refactor_plan.layout import detect_layout

    layout = detect_layout(tmp_path)
    assert layout.has_tests is False


# ---------------------------------------------------------------------------
# validator — layout-aware structural and behavioral modes
# ---------------------------------------------------------------------------

def test_validate_structural_uses_source_root(tmp_path: Path) -> None:
    from refactor_plan.layout import ProjectLayout
    from refactor_plan.validation.validator import validate

    src = tmp_path / "src"
    src.mkdir()
    layout = ProjectLayout(
        source_root=src,
        cluster_root=src,
        test_roots=[],
        has_tests=False,
        root_package="",
    )

    report = validate(tmp_path, mode="structural", layout=layout)
    commands = [r.command for r in report.commands]
    assert any("src" in c for c in commands), f"expected source_root in compile target: {commands}"
    assert not any(c.endswith("compileall .") for c in commands)


def test_validate_behavioral_installability_check(tmp_path: Path) -> None:
    from refactor_plan.layout import ProjectLayout
    from refactor_plan.validation.validator import validate

    src = tmp_path / "src"
    src.mkdir()
    pkg = src / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_dummy.py").write_text("def test_noop(): pass\n")

    layout = ProjectLayout(
        source_root=src,
        cluster_root=pkg,
        test_roots=[tests],
        has_tests=True,
        root_package="mypkg",
    )

    report = validate(tmp_path, mode="behavioral", layout=layout)
    commands = [r.command for r in report.commands]
    import_idx = next((i for i, c in enumerate(commands) if "import mypkg" in c), None)
    pytest_idx = next((i for i, c in enumerate(commands) if "pytest" in c), None)
    assert import_idx is not None, "importability check must run"
    assert pytest_idx is not None, "pytest must run when tests exist"
    assert import_idx < pytest_idx, "import check must precede pytest"
    assert report.passed
