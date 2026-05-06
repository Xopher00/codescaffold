"""Tests for codescaffold.candidates — MoveCandidate and propose_moves."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from codescaffold.candidates import MoveCandidate, propose_moves
from codescaffold.graphify.snapshot import GraphSnapshot


# ---------------------------------------------------------------------------
# Helpers: build controlled GraphSnapshots
# ---------------------------------------------------------------------------

def _make_snapshot_with_tension() -> GraphSnapshot:
    """A graph where community 1 has low cohesion and a misplaced node.

    Community 0: a1, a2, a3 — tightly connected (cohesion = 1.0), src/auth.py
    Community 1: b1, b2, b3, x1..x5 — b nodes dense internally; x nodes have
                 ALL their edges pointing into community 0 (cross-community).
                 Cohesion = 3/28 ≈ 0.11 < threshold of 0.15.

    x1 (label="misplaced_func", src/billing.py) connects only to a1 in community 0,
    so it should be proposed as a candidate to move to src/auth.py.
    """
    G = nx.Graph()
    # Community 0: tightly connected
    for n in ["a1", "a2", "a3"]:
        G.add_node(n, label=n, source_file="src/auth.py")
    G.add_edge("a1", "a2")
    G.add_edge("a2", "a3")
    G.add_edge("a1", "a3")

    # Community 1: b cluster dense internally; x nodes connect outward only
    for n in ["b1", "b2", "b3"]:
        G.add_node(n, label=n, source_file="src/billing.py")
    G.add_edge("b1", "b2")
    G.add_edge("b2", "b3")
    G.add_edge("b1", "b3")

    # x nodes all in community 1, source_file billing.py, but edges point into community 0
    for n in ["x1", "x2", "x3", "x4", "x5"]:
        G.add_node(n, label=n if n != "x1" else "misplaced_func", source_file="src/billing.py")
    G.add_edge("x1", "a1")   # x1 pulls toward auth
    G.add_edge("x2", "a1")
    G.add_edge("x3", "a2")
    G.add_edge("x4", "a3")
    G.add_edge("x5", "a1")

    # community 1 has 8 nodes, 3 intra edges (b cluster) → cohesion = 3/28 ≈ 0.11
    communities = {
        0: ["a1", "a2", "a3"],
        1: ["b1", "b2", "b3", "x1", "x2", "x3", "x4", "x5"],
    }
    from codescaffold.graphify.snapshot import _hash_graph
    return GraphSnapshot(graph=G, communities=communities, graph_hash=_hash_graph(G))


def _make_empty_snapshot() -> GraphSnapshot:
    G = nx.Graph()
    from codescaffold.graphify.snapshot import _hash_graph
    return GraphSnapshot(graph=G, communities={}, graph_hash=_hash_graph(G))


def _make_high_cohesion_snapshot() -> GraphSnapshot:
    """All nodes in one tight community — no candidates expected."""
    G = nx.Graph()
    for n in ["a", "b", "c", "d"]:
        G.add_node(n, label=n, source_file="src/main.py")
    for u, v in [("a", "b"), ("b", "c"), ("c", "d"), ("a", "d"), ("a", "c"), ("b", "d")]:
        G.add_edge(u, v)
    communities = {0: ["a", "b", "c", "d"]}
    from codescaffold.graphify.snapshot import _hash_graph
    return GraphSnapshot(graph=G, communities=communities, graph_hash=_hash_graph(G))


# ---------------------------------------------------------------------------
# MoveCandidate model
# ---------------------------------------------------------------------------

class TestMoveCandidateModel:
    def test_frozen(self):
        c = MoveCandidate(
            kind="symbol",
            source_file="src/utils.py",
            symbol="helper",
            target_file="src/core.py",
            community_id=0,
            reasons=("low cohesion",),
            confidence="high",
        )
        with pytest.raises((AttributeError, TypeError)):
            c.symbol = "other"  # type: ignore[misc]

    def test_module_kind_has_no_symbol(self):
        c = MoveCandidate(
            kind="module",
            source_file="src/utils.py",
            symbol=None,
            target_file="src/",
            community_id=1,
            reasons=("majority of nodes point elsewhere",),
            confidence="medium",
        )
        assert c.symbol is None

    def test_reasons_is_tuple(self):
        c = MoveCandidate(
            kind="symbol",
            source_file="src/a.py",
            symbol="Foo",
            target_file="src/b.py",
            community_id=0,
            reasons=("reason one", "reason two"),
            confidence="low",
        )
        assert isinstance(c.reasons, tuple)


# ---------------------------------------------------------------------------
# propose_moves
# ---------------------------------------------------------------------------

class TestProposeMoves:
    def test_returns_list(self):
        snap = _make_empty_snapshot()
        result = propose_moves(snap)
        assert isinstance(result, list)

    def test_empty_graph_returns_no_candidates(self):
        result = propose_moves(_make_empty_snapshot())
        assert result == []

    def test_high_cohesion_returns_no_candidates(self):
        result = propose_moves(_make_high_cohesion_snapshot())
        assert result == []

    def test_misplaced_node_detected(self):
        snap = _make_snapshot_with_tension()
        result = propose_moves(snap)
        # The misplaced 'x' node should appear as a candidate
        symbols = [c.symbol for c in result]
        assert "misplaced_func" in symbols

    def test_candidate_fields_valid(self):
        snap = _make_snapshot_with_tension()
        for c in propose_moves(snap):
            assert isinstance(c, MoveCandidate)
            assert c.source_file
            assert c.target_file
            assert c.source_file != c.target_file
            assert c.confidence in ("high", "medium", "low")
            assert isinstance(c.reasons, tuple)
            assert len(c.reasons) > 0

    def test_no_duplicate_candidates(self):
        snap = _make_snapshot_with_tension()
        candidates = propose_moves(snap)
        keys = [(c.source_file, c.symbol) for c in candidates]
        assert len(keys) == len(set(keys))

    def test_target_file_from_target_community(self):
        snap = _make_snapshot_with_tension()
        candidates = propose_moves(snap)
        misplaced = next((c for c in candidates if c.symbol == "misplaced_func"), None)
        assert misplaced is not None
        # x is pulled toward billing community — target should be src/billing.py
        assert misplaced.target_file == "src/auth.py"

    def test_works_on_real_repo(self, messy_repo: Path):
        from codescaffold.graphify import run_extract
        snap = run_extract(messy_repo)
        result = propose_moves(snap)
        # May or may not produce candidates for a small repo — just shouldn't crash
        assert isinstance(result, list)
        assert all(isinstance(c, MoveCandidate) for c in result)
