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
    return plan(view, FIXTURE_REPO, FIXTURE_GRAPH)


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
# 5. Symbol moves: 5 non-method moves (A1 filters out .echo()), all unapproved
# ---------------------------------------------------------------------------

def test_five_symbol_moves(refactor_plan):
    # A1: method labels (starting with ".") are filtered — .echo() is excluded.
    assert len(refactor_plan.symbol_moves) == 5


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
    plan_a = plan(view, FIXTURE_REPO, FIXTURE_GRAPH)
    plan_b = plan(view, FIXTURE_REPO, FIXTURE_GRAPH)
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


# ---------------------------------------------------------------------------
# A1 — Methods must not appear in symbol_moves
# ---------------------------------------------------------------------------

def test_no_method_labels_in_symbol_moves(refactor_plan):
    """A1: Labels starting with '.' (bound methods) must be filtered out."""
    for sm in refactor_plan.symbol_moves:
        assert not sm.label.startswith("."), (
            f"Method label {sm.label!r} leaked into symbol_moves; "
            "rope cannot move bound methods"
        )


# ---------------------------------------------------------------------------
# A2 — Shim candidates: vec.py and reader.py trigger "in __all__"
# ---------------------------------------------------------------------------

def test_shim_candidates_include_vec_and_reader(refactor_plan):
    """A2: vec.py defines Vec and reader.py defines Reader, both in __all__."""
    shim_srcs = {sc.src for sc in refactor_plan.shim_candidates}
    vec_shims = [sc for sc in refactor_plan.shim_candidates if sc.src.endswith("vec.py")]
    reader_shims = [sc for sc in refactor_plan.shim_candidates if sc.src.endswith("reader.py")]
    assert len(vec_shims) >= 1, f"Expected vec.py as shim candidate, got srcs: {shim_srcs}"
    assert len(reader_shims) >= 1, f"Expected reader.py as shim candidate, got srcs: {shim_srcs}"
    for sc in vec_shims:
        assert "in __all__" in sc.triggers, f"Expected 'in __all__' trigger for {sc.src}"
    for sc in reader_shims:
        assert "in __all__" in sc.triggers, f"Expected 'in __all__' trigger for {sc.src}"


def test_shim_candidates_count_at_least_two(refactor_plan):
    """A2: At least 2 shim candidates (vec.py and reader.py)."""
    assert len(refactor_plan.shim_candidates) >= 2


# ---------------------------------------------------------------------------
# A3 — dest_file is populated and consistent
# ---------------------------------------------------------------------------

def test_symbol_moves_have_dest_file(refactor_plan):
    """A3: Every SymbolMove must have a non-empty dest_file."""
    for sm in refactor_plan.symbol_moves:
        assert sm.dest_file, f"SymbolMove {sm.label!r} has empty dest_file"


def test_symbol_moves_dest_file_starts_with_dest_cluster(refactor_plan):
    """A3: dest_file must start with dest_cluster + '/'."""
    for sm in refactor_plan.symbol_moves:
        assert sm.dest_file.startswith(sm.dest_cluster + "/"), (
            f"dest_file {sm.dest_file!r} does not start with dest_cluster {sm.dest_cluster!r}/"
        )


def test_symbol_moves_no_unsorted_dest_file(refactor_plan):
    """A3: No dest_file should end with _unsorted.py (abolished placeholder)."""
    for sm in refactor_plan.symbol_moves:
        assert not sm.dest_file.endswith("_unsorted.py"), (
            f"SymbolMove {sm.label!r} still uses _unsorted.py: {sm.dest_file!r}"
        )


def test_read_first_line_dest_file(refactor_plan):
    """A3: read_first_line() should land in pkg_003/reader.py (community 2 has only reader.py)."""
    sm = next(
        (s for s in refactor_plan.symbol_moves if "read_first_line" in s.label), None
    )
    assert sm is not None, "read_first_line() not found in symbol_moves"
    assert sm.dest_file == "pkg_003/reader.py", (
        f"Expected dest_file='pkg_003/reader.py', got {sm.dest_file!r}"
    )


def test_vec_from_pair_dest_file(refactor_plan):
    """A3: vec_from_pair() should land in pkg_004/vec.py (community 3 has only vec.py)."""
    sm = next(
        (s for s in refactor_plan.symbol_moves if "vec_from_pair" in s.label), None
    )
    assert sm is not None, "vec_from_pair() not found in symbol_moves"
    assert sm.dest_file == "pkg_004/vec.py", (
        f"Expected dest_file='pkg_004/vec.py', got {sm.dest_file!r}"
    )


def test_distance_dest_file(refactor_plan):
    """A3: distance() should land in pkg_004/vec.py (community 3 has only vec.py)."""
    sm = next(
        (s for s in refactor_plan.symbol_moves if "distance" in s.label), None
    )
    assert sm is not None, "distance() not found in symbol_moves"
    assert sm.dest_file == "pkg_004/vec.py", (
        f"Expected dest_file='pkg_004/vec.py', got {sm.dest_file!r}"
    )
