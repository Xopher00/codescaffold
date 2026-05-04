from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from refactor_plan.contracts.import_contracts import (
    _derive_independence_contracts,
    _derive_layers_contract,
    _find_cycles,
    _is_hand_edited,
    check_staleness,
    generate_contracts,
)
from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.layout import ProjectLayout
from refactor_plan.planning.models import RefactorPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_layout(tmp_path: Path, root_package: str = "mypkg") -> ProjectLayout:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    return ProjectLayout(
        source_root=src,
        cluster_root=src / root_package,
        test_roots=[tmp_path / "tests"],
        has_tests=False,
        root_package=root_package,
    )


def _make_view(tmp_path: Path) -> ClusterView:
    G = nx.DiGraph()
    return ClusterView(
        file_communities={},
        G=G,
        cohesion={},
        god_nodes=[],
        surprising_connections=[],
    )


def _make_graph_json(tmp_path: Path) -> Path:
    p = tmp_path / "graph.json"
    p.write_text(json.dumps({"nodes": [], "edges": []}))
    return p


# ---------------------------------------------------------------------------
# _find_cycles
# ---------------------------------------------------------------------------

def test_find_cycles_detects_simple_cycle():
    pkg_map = {"a": {"b"}, "b": {"a"}, "c": set()}
    cycles = _find_cycles(pkg_map)
    assert any(set(c) == {"a", "b"} for c in cycles)


def test_find_cycles_empty_when_acyclic():
    pkg_map = {"a": {"b"}, "b": {"c"}, "c": set()}
    assert _find_cycles(pkg_map) == []


# ---------------------------------------------------------------------------
# _derive_layers_contract
# ---------------------------------------------------------------------------

def test_derive_layers_acyclic():
    pkg_map = {"high": {"mid"}, "mid": {"low"}, "low": set()}
    spec = _derive_layers_contract(pkg_map, "pkg")
    assert spec is not None
    assert spec.contract_type == "layers"
    # high-level importers come first (generation 0)
    assert spec.layers[0] == ["pkg.high"]
    assert spec.layers[-1] == ["pkg.low"]


def test_derive_layers_none_when_cycles():
    pkg_map = {"a": {"b"}, "b": {"a"}}
    assert _derive_layers_contract(pkg_map, "pkg") is None


# ---------------------------------------------------------------------------
# _derive_independence_contracts
# ---------------------------------------------------------------------------

def test_independence_finds_unlinked_packages():
    # a→b, c and d have no links to anyone
    pkg_map = {
        "a": {"b"},
        "b": {"a"},   # cycle between a and b
        "c": set(),
        "d": set(),
    }
    # b and a are in cycle; c and d are imported by nobody → both are entry-point-like
    # but neither imports the other → should form an independence group if they are "library"
    # Since c and d have no incoming edges, they ARE excluded as entry points.
    # If the library set is empty, no contract is generated.
    result = _derive_independence_contracts(pkg_map, "pkg")
    # With only entry-point packages left after filtering, no contract expected
    assert result == []


def test_independence_with_library_packages():
    # lib_a and lib_b are imported by entry (entry is excluded)
    # lib_a and lib_b don't import each other → independent
    pkg_map = {
        "entry": {"lib_a", "lib_b"},
        "lib_a": set(),
        "lib_b": set(),
    }
    result = _derive_independence_contracts(pkg_map, "mypkg")
    assert len(result) == 1
    assert result[0].contract_type == "independence"
    assert "mypkg.lib_a" in result[0].modules
    assert "mypkg.lib_b" in result[0].modules


def test_independence_excludes_linked_pairs():
    pkg_map = {
        "entry": {"lib_a", "lib_b", "lib_c"},
        "lib_a": {"lib_b"},   # lib_a imports lib_b → not independent
        "lib_b": set(),
        "lib_c": set(),
    }
    result = _derive_independence_contracts(pkg_map, "mypkg")
    assert len(result) == 1
    # lib_a conflicts with lib_b; best independence set has size 2, excluding one of the pair
    modules = set(result[0].modules)
    assert len(modules) == 2
    assert not ({"mypkg.lib_a", "mypkg.lib_b"} <= modules), "conflicting pair should not both appear"


# ---------------------------------------------------------------------------
# check_staleness
# ---------------------------------------------------------------------------

def test_check_staleness_missing_file(tmp_path: Path):
    config = tmp_path / ".importlinter"
    graph_json = tmp_path / "graph.json"
    graph_json.write_text("{}")
    is_stale, reason = check_staleness(config, graph_json)
    assert is_stale
    assert "run contracts" in reason.lower()


def test_check_staleness_hand_edited(tmp_path: Path):
    config = tmp_path / ".importlinter"
    config.write_text("[importlinter]\nroot_packages = mypkg\n")
    graph_json = tmp_path / "graph.json"
    graph_json.write_text("{}")
    is_stale, reason = check_staleness(config, graph_json)
    assert not is_stale
    assert "hand-edited" in reason


def test_check_staleness_up_to_date(tmp_path: Path):
    import datetime
    graph_json = tmp_path / "graph.json"
    graph_json.write_text("{}")
    mtime = datetime.datetime.fromtimestamp(
        graph_json.stat().st_mtime
    ).isoformat(timespec="seconds")
    config = tmp_path / ".importlinter"
    config.write_text(
        f"# AUTO-GENERATED by codescaffold on {mtime}\n"
        f"# Graph: graph.json (mtime: {mtime})\n"
        "[importlinter]\nroot_packages = mypkg\n"
    )
    is_stale, reason = check_staleness(config, graph_json)
    assert not is_stale


def test_check_staleness_stale(tmp_path: Path):
    config = tmp_path / ".importlinter"
    config.write_text(
        "# AUTO-GENERATED by codescaffold on 2020-01-01T00:00:00\n"
        "# Graph: graph.json (mtime: 2020-01-01T00:00:00)\n"
        "[importlinter]\nroot_packages = mypkg\n"
    )
    graph_json = tmp_path / "graph.json"
    graph_json.write_text("{}")
    is_stale, reason = check_staleness(config, graph_json)
    assert is_stale
    assert "2020-01-01" in reason


# ---------------------------------------------------------------------------
# _is_hand_edited
# ---------------------------------------------------------------------------

def test_is_hand_edited_true_for_manual_file(tmp_path: Path):
    f = tmp_path / ".importlinter"
    f.write_text("[importlinter]\nroot_packages = mypkg\n")
    assert _is_hand_edited(f) is True


def test_is_hand_edited_false_for_generated(tmp_path: Path):
    f = tmp_path / ".importlinter"
    f.write_text("# AUTO-GENERATED by codescaffold on 2026-01-01T00:00:00\n")
    assert _is_hand_edited(f) is False


def test_is_hand_edited_false_when_missing(tmp_path: Path):
    f = tmp_path / ".importlinter"
    assert _is_hand_edited(f) is False


# ---------------------------------------------------------------------------
# generate_contracts — integration
# ---------------------------------------------------------------------------

def test_generate_contracts_writes_provenance(tmp_path: Path):
    layout = _make_layout(tmp_path)
    view = _make_view(tmp_path)
    graph_json = _make_graph_json(tmp_path)
    plan = RefactorPlan(clusters=[], file_moves=[], symbol_moves=[])

    artifact = generate_contracts(plan, view, graph_json, tmp_path, layout, force=True)

    config = Path(artifact.config_path)
    assert config.exists()
    content = config.read_text()
    assert "AUTO-GENERATED by codescaffold" in content
    assert "DO NOT EDIT" in content
    assert layout.root_package in content


def test_generate_contracts_skips_hand_edited_without_force(tmp_path: Path):
    config = tmp_path / ".importlinter"
    config.write_text("[importlinter]\nroot_packages = mypkg\n")

    layout = _make_layout(tmp_path)
    view = _make_view(tmp_path)
    graph_json = _make_graph_json(tmp_path)
    plan = RefactorPlan(clusters=[], file_moves=[], symbol_moves=[])

    artifact = generate_contracts(plan, view, graph_json, tmp_path, layout, force=False)

    assert artifact.was_hand_edited
    assert artifact.skipped_reason
    assert config.read_text() == "[importlinter]\nroot_packages = mypkg\n"


def test_generate_contracts_force_overwrites_hand_edited(tmp_path: Path):
    config = tmp_path / ".importlinter"
    config.write_text("[importlinter]\nroot_packages = mypkg\n")

    layout = _make_layout(tmp_path)
    view = _make_view(tmp_path)
    graph_json = _make_graph_json(tmp_path)
    plan = RefactorPlan(clusters=[], file_moves=[], symbol_moves=[])

    artifact = generate_contracts(plan, view, graph_json, tmp_path, layout, force=True)

    assert not artifact.was_hand_edited
    assert "AUTO-GENERATED" in config.read_text()
