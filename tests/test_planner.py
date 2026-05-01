"""Tests for planner.py.

Fixture: tests/fixtures/messy_repo  (graph.json with 5 communities, 6 misplaced symbols)

Communities from cluster_view:
  id=0 → 4 files  → pkg_001  (largest)
  id=1 → 2 files  → pkg_002
  id=2 → 1 file   → pkg_003
  id=3 → 1 file   → pkg_004
  id=4 → 1 file   → pkg_005
"""

from pathlib import Path

import pytest

from refactor_plan.cluster_view import build_view
from refactor_plan.planner import (
    RefactorPlan,
    plan,
    write_plan,
)

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
# 1. Returns RefactorPlan
# ---------------------------------------------------------------------------

def test_plan_returns_refactor_plan(refactor_plan):
    assert isinstance(refactor_plan, RefactorPlan)


# ---------------------------------------------------------------------------
# 2. Cluster count matches fixture's 5 communities
# ---------------------------------------------------------------------------

def test_five_clusters(refactor_plan):
    assert len(refactor_plan.clusters) == 5


# ---------------------------------------------------------------------------
# 3. Largest cluster (community_id=0, 4 files) gets pkg_001
# ---------------------------------------------------------------------------

def test_largest_cluster_is_pkg_001(refactor_plan):
    pkg_001 = next(c for c in refactor_plan.clusters if c.name == "pkg_001")
    assert pkg_001.community_id == 0
    assert len(pkg_001.files) == 4


# ---------------------------------------------------------------------------
# 4. At least one non-trivial file move emitted
# ---------------------------------------------------------------------------

def test_at_least_one_file_move(refactor_plan):
    assert len(refactor_plan.file_moves) >= 1
    # Every file move must have a cluster name and non-empty src/dest.
    for fm in refactor_plan.file_moves:
        assert fm.src != fm.dest
        assert fm.cluster.startswith("pkg_")
        assert fm.dest.startswith(fm.cluster + "/")


# ---------------------------------------------------------------------------
# 5. Exactly 6 symbol moves, all unapproved, specific labels present
# ---------------------------------------------------------------------------

def test_six_symbol_moves(refactor_plan):
    assert len(refactor_plan.symbol_moves) == 6


def test_symbol_moves_all_unapproved(refactor_plan):
    for sm in refactor_plan.symbol_moves:
        assert sm.approved is False


def test_symbol_moves_include_expected_labels(refactor_plan):
    labels = {sm.label for sm in refactor_plan.symbol_moves}
    assert "vec_from_pair()" in labels
    assert "read_first_line()" in labels


def test_symbol_moves_dest_cluster_is_pkg_name(refactor_plan):
    valid_names = {c.name for c in refactor_plan.clusters}
    for sm in refactor_plan.symbol_moves:
        assert sm.dest_cluster in valid_names


# ---------------------------------------------------------------------------
# 6. Shim candidates: every emitted candidate has at least one trigger
#    (Do not over-constrain — the fixture may produce zero candidates.)
# ---------------------------------------------------------------------------

def test_shim_candidates_have_triggers(refactor_plan):
    assert len(refactor_plan.shim_candidates) >= 0
    for sc in refactor_plan.shim_candidates:
        assert len(sc.triggers) >= 1, f"ShimCandidate for {sc.src} has no triggers"


def test_shim_candidates_are_sorted(refactor_plan):
    srcs = [sc.src for sc in refactor_plan.shim_candidates]
    assert srcs == sorted(srcs)


# ---------------------------------------------------------------------------
# 7. At least one splitting candidate (bridge_node entries present in fixture)
# ---------------------------------------------------------------------------

def test_at_least_one_splitting_candidate(refactor_plan):
    assert len(refactor_plan.splitting_candidates) >= 1


def test_splitting_candidates_types_are_valid(refactor_plan):
    valid_types = {"low_cohesion", "bridge_node"}
    for sc in refactor_plan.splitting_candidates:
        assert sc.type in valid_types


def test_splitting_candidates_are_sorted(refactor_plan):
    keys = [(c.type, c.question) for c in refactor_plan.splitting_candidates]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# 8. Determinism: calling plan() twice gives identical model_dump()
# ---------------------------------------------------------------------------

def test_plan_is_deterministic(view):
    plan_a = plan(view, FIXTURE_REPO)
    plan_b = plan(view, FIXTURE_REPO)
    assert plan_a.model_dump() == plan_b.model_dump()


# ---------------------------------------------------------------------------
# 9. write_plan round-trip
# ---------------------------------------------------------------------------

def test_write_plan_round_trip(refactor_plan, tmp_path):
    out = tmp_path / "p.json"
    write_plan(refactor_plan, out)
    recovered = RefactorPlan.model_validate_json(out.read_text())
    assert recovered.model_dump() == refactor_plan.model_dump()


# ---------------------------------------------------------------------------
# 10. Cluster allocation ordering sanity
# ---------------------------------------------------------------------------

def test_cluster_ordering_by_size(refactor_plan):
    """Clusters should be ordered largest-first (ties broken by lowest id)."""
    sizes = [len(c.files) for c in refactor_plan.clusters]
    # sizes should be non-increasing
    for i in range(len(sizes) - 1):
        assert sizes[i] >= sizes[i + 1], (
            f"Cluster ordering violated at index {i}: {sizes}"
        )
