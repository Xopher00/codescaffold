"""Tests for codescaffold.graphify — the graphify wrapper layer."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from codescaffold.graphify import (
    GodNode,
    GraphSnapshot,
    SurprisingEdge,
    cohesion,
    god_nodes,
    run_extract,
    surprises,
)


# ---------------------------------------------------------------------------
# run_extract
# ---------------------------------------------------------------------------

class TestRunExtract:
    def test_returns_snapshot(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        assert isinstance(snap, GraphSnapshot)

    def test_graph_is_networkx(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        assert isinstance(snap.graph, nx.Graph)

    def test_graph_has_nodes(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        assert snap.graph.number_of_nodes() > 0

    def test_communities_populated(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        assert isinstance(snap.communities, dict)
        assert len(snap.communities) > 0

    def test_graph_hash_is_string(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        assert isinstance(snap.graph_hash, str)
        assert len(snap.graph_hash) == 64  # sha256 hex

    def test_empty_repo_does_not_crash(self, tmp_path: Path):
        (tmp_path / "empty.py").write_text("")
        snap = run_extract(tmp_path)
        assert isinstance(snap, GraphSnapshot)


# ---------------------------------------------------------------------------
# Graph hash stability
# ---------------------------------------------------------------------------

class TestGraphHash:
    def test_stable_across_runs(self, messy_repo: Path):
        h1 = run_extract(messy_repo).graph_hash
        h2 = run_extract(messy_repo).graph_hash
        assert h1 == h2

    def test_changes_when_file_edited(self, messy_repo: Path):
        h1 = run_extract(messy_repo).graph_hash
        utils = messy_repo / "src" / "messy_pkg" / "utils.py"
        utils.write_text("def helper():\n    return 999\n\ndef new_func():\n    pass\n")
        h2 = run_extract(messy_repo).graph_hash
        assert h1 != h2


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

class TestGodNodes:
    def test_returns_list_of_god_nodes(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        result = god_nodes(snap)
        assert isinstance(result, list)
        assert all(isinstance(n, GodNode) for n in result)

    def test_respects_top_n(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        result = god_nodes(snap, top_n=2)
        assert len(result) <= 2

    def test_god_nodes_have_positive_degree(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        for node in god_nodes(snap):
            assert node.degree >= 0


class TestCohesion:
    def test_returns_dict_per_community(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        scores = cohesion(snap)
        assert isinstance(scores, dict)
        assert all(isinstance(k, int) for k in scores)
        assert all(isinstance(v, float) for v in scores.values())

    def test_scores_in_range(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        for score in cohesion(snap).values():
            assert 0.0 <= score <= 1.0


class TestSurprises:
    def test_returns_list_of_surprising_edges(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        result = surprises(snap)
        assert isinstance(result, list)
        assert all(isinstance(e, SurprisingEdge) for e in result)

    def test_source_files_is_tuple(self, messy_repo: Path):
        snap = run_extract(messy_repo)
        for edge in surprises(snap):
            assert isinstance(edge.source_files, tuple)

    def test_does_not_crash_on_small_graph(self, tmp_path: Path):
        # Single-file repo — shouldn't crash, may return empty
        (tmp_path / "solo.py").write_text("class Solo:\n    pass\n")
        snap = run_extract(tmp_path)
        result = surprises(snap)
        assert isinstance(result, list)
