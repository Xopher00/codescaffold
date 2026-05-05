from __future__ import annotations

from pathlib import Path

import networkx as nx
from refactor_plan.interface import ClusterView
from refactor_plan.planning import plan as build_plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_view(
    communities: dict[int, list[str]],
    cohesion: dict[int, float] | None = None,
    surprising: list[dict] | None = None,
) -> ClusterView:
    G = nx.DiGraph()
    for files in communities.values():
        for f in files:
            G.add_node(f, source_file=f, label=Path(f).name, file_type="code")
    return ClusterView(
        file_communities=communities,
        G=G,
        cohesion=cohesion or {},
        god_nodes=[],
        surprising_connections=surprising or [],
    )


def _tmp_files(tmp_path: Path, paths: list[str]) -> list[Path]:
    """Create stub Python files and return their absolute paths."""
    created = []
    for rel in paths:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# stub\n")
        created.append(p)
    return created


# ---------------------------------------------------------------------------
# PendingDecision — multi-directory community
# ---------------------------------------------------------------------------

def test_plan_emits_pending_decision_for_spread_community(tmp_path: Path) -> None:
    _tmp_files(tmp_path, [
        "src/mypkg/contracts/a.py",
        "src/mypkg/contracts/b.py",
        "src/mypkg/interface/c.py",   # outlier
    ])
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "mypkg" / "contracts" / "__init__.py").write_text("")
    (tmp_path / "src" / "mypkg" / "interface" / "__init__.py").write_text("")

    communities = {
        0: [
            str(tmp_path / "src/mypkg/contracts/a.py"),
            str(tmp_path / "src/mypkg/contracts/b.py"),
            str(tmp_path / "src/mypkg/interface/c.py"),
        ]
    }
    view = _make_view(communities)
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    assert result.file_moves == [], "file_moves must be empty — populated by approve_moves"
    assert len(result.pending_decisions) == 1
    pd = result.pending_decisions[0]
    assert pd.community_id == 0
    assert pd.needs_placement is True
    assert len(pd.current_dirs) == 2


def test_plan_emits_no_pending_decision_for_single_file_community(tmp_path: Path) -> None:
    _tmp_files(tmp_path, ["src/mypkg/contracts/solo.py"])
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")

    communities = {0: [str(tmp_path / "src/mypkg/contracts/solo.py")]}
    view = _make_view(communities)
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    assert result.file_moves == []
    assert result.pending_decisions == []


def test_plan_pending_decision_needs_placement_false_when_co_located(tmp_path: Path) -> None:
    _tmp_files(tmp_path, [
        "src/mypkg/contracts/a.py",
        "src/mypkg/contracts/b.py",
        "src/mypkg/contracts/c.py",
    ])
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "mypkg" / "contracts" / "__init__.py").write_text("")

    communities = {
        0: [
            str(tmp_path / "src/mypkg/contracts/a.py"),
            str(tmp_path / "src/mypkg/contracts/b.py"),
            str(tmp_path / "src/mypkg/contracts/c.py"),
        ]
    }
    view = _make_view(communities)
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    assert result.file_moves == []
    assert len(result.pending_decisions) == 1
    pd = result.pending_decisions[0]
    assert pd.needs_placement is False
    assert len(pd.current_dirs) == 1


def test_plan_surprising_connections_attached_to_community(tmp_path: Path) -> None:
    files = _tmp_files(tmp_path, [
        "src/mypkg/contracts/a.py",
        "src/mypkg/planning/b.py",
    ])
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")

    f_a = str(files[0])
    f_b = str(files[1])
    surprise = {"source": "a", "target": "b", "source_file": f_a, "confidence": "INFERRED", "confidence_score": 0.7}

    communities = {0: [f_a, f_b]}
    view = _make_view(communities, surprising=[surprise])
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    assert len(result.pending_decisions) == 1
    pd = result.pending_decisions[0]
    assert any(s["source_file"] == f_a for s in pd.surprising_connections)


def test_plan_cross_cluster_edges_not_self_referential(tmp_path: Path) -> None:
    files = _tmp_files(tmp_path, [
        "src/mypkg/contracts/a.py",
        "src/mypkg/contracts/b.py",
        "src/mypkg/planning/c.py",  # different community
    ])
    (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")

    f_a, f_b, f_c = [str(f) for f in files]
    G = nx.DiGraph()
    for f in [f_a, f_b, f_c]:
        G.add_node(f, source_file=f, label=Path(f).name)
    G.add_edge(f_a, f_c, relation="imports", weight=2.0)  # cross-cluster
    G.add_edge(f_a, f_b, relation="calls", weight=1.0)    # same-cluster — should be excluded

    view = ClusterView(
        file_communities={0: [f_a, f_b], 1: [f_c]},
        G=G,
        cohesion={},
        god_nodes=[],
        surprising_connections=[],
    )
    result = build_plan(view, tmp_path, tmp_path / "graph.json")

    pd = result.pending_decisions[0]
    for edge in pd.cross_cluster_edges:
        assert edge["target_file"] not in {f_a, f_b}, "same-community edge should not appear in cross_cluster_edges"
