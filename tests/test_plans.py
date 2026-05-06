"""Tests for codescaffold.plans — schema, persistence, and staleness."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from codescaffold.candidates.models import MoveCandidate
from codescaffold.graphify.snapshot import GraphSnapshot, _hash_graph
from codescaffold.plans import (
    ApprovedMove,
    CandidateRecord,
    Plan,
    StalePlanError,
    assert_fresh,
    load,
    save,
)
from codescaffold.plans.store import candidates_to_records, records_to_candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_snapshot(extra_node: str | None = None) -> GraphSnapshot:
    G = nx.Graph()
    G.add_node("a", label="Foo", source_file="src/foo.py")
    G.add_node("b", label="Bar", source_file="src/bar.py")
    G.add_edge("a", "b")
    if extra_node:
        G.add_node(extra_node, label=extra_node, source_file="src/extra.py")
    return GraphSnapshot(graph=G, communities={0: ["a", "b"]}, graph_hash=_hash_graph(G))


def _sample_candidate() -> MoveCandidate:
    return MoveCandidate(
        kind="symbol",
        source_file="src/auth.py",
        symbol="helper",
        target_file="src/utils.py",
        community_id=1,
        reasons=("low cohesion",),
        confidence="high",
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestPlanSchema:
    def test_plan_is_immutable(self):
        from pydantic import ValidationError
        snap = _simple_snapshot()
        plan = Plan(graph_hash=snap.graph_hash)
        with pytest.raises((AttributeError, TypeError, ValidationError)):
            plan.graph_hash = "other"  # type: ignore[misc]

    def test_defaults_populated(self):
        plan = Plan(graph_hash="abc123")
        assert plan.candidates == []
        assert plan.approved_moves == []
        assert plan.created_at is not None

    def test_with_candidates_and_moves(self):
        snap = _simple_snapshot()
        records = candidates_to_records([_sample_candidate()])
        moves = [ApprovedMove(kind="symbol", source_file="src/auth.py", symbol="helper", target_file="src/utils.py")]
        plan = Plan(graph_hash=snap.graph_hash, candidates=records, approved_moves=moves)
        assert len(plan.candidates) == 1
        assert len(plan.approved_moves) == 1


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------

class TestPlanStore:
    def test_round_trip(self, tmp_path: Path):
        snap = _simple_snapshot()
        records = candidates_to_records([_sample_candidate()])
        plan = Plan(graph_hash=snap.graph_hash, candidates=records)
        plan_path = tmp_path / "plan.json"
        save(plan, plan_path)
        loaded = load(plan_path)
        assert loaded.graph_hash == plan.graph_hash
        assert len(loaded.candidates) == 1
        assert loaded.candidates[0].symbol == "helper"

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        plan = Plan(graph_hash="abc")
        plan_path = tmp_path / "nested" / "deep" / "plan.json"
        save(plan, plan_path)
        assert plan_path.exists()

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "nonexistent.json")

    def test_candidates_to_records_round_trip(self):
        original = [_sample_candidate()]
        records = candidates_to_records(original)
        restored = records_to_candidates(records)
        assert len(restored) == 1
        r = restored[0]
        assert r.kind == original[0].kind
        assert r.source_file == original[0].source_file
        assert r.symbol == original[0].symbol
        assert r.reasons == original[0].reasons
        assert r.confidence == original[0].confidence


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------

class TestAssertFresh:
    def test_passes_when_hash_matches(self):
        snap = _simple_snapshot()
        plan = Plan(graph_hash=snap.graph_hash)
        assert_fresh(plan, snap)  # should not raise

    def test_raises_stale_plan_error_when_hash_differs(self):
        snap1 = _simple_snapshot()
        snap2 = _simple_snapshot(extra_node="new_node")
        plan = Plan(graph_hash=snap1.graph_hash)
        with pytest.raises(StalePlanError) as exc_info:
            assert_fresh(plan, snap2)
        err = exc_info.value
        assert err.stored_hash == snap1.graph_hash
        assert err.current_hash == snap2.graph_hash

    def test_stale_error_message_includes_hashes(self):
        snap1 = _simple_snapshot()
        snap2 = _simple_snapshot(extra_node="x")
        plan = Plan(graph_hash=snap1.graph_hash)
        with pytest.raises(StalePlanError, match="stale"):
            assert_fresh(plan, snap2)
