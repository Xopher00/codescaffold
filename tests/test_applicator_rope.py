"""Tests for applicator/rope_runner.py.

Tests use a copy of the messy_repo fixture so original is never mutated.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from refactor_plan.cluster_view import build_view
from refactor_plan.planner import plan
from refactor_plan.applicator.rope_runner import (
    ApplyResult,
    apply_plan,
    rollback,
)

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_copy(tmp_path):
    """Return a fresh copy of the messy_repo fixture."""
    src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(".refactor_plan", "graphify-out"),
    )
    # Copy the graph.json so build_view works
    refplan_dir = dst / ".refactor_plan"
    refplan_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_GRAPH, refplan_dir / "graph.json")
    return dst


@pytest.fixture
def view():
    return build_view(FIXTURE_GRAPH)


@pytest.fixture
def refactor_plan(view):
    return plan(view, FIXTURE_REPO)


# ---------------------------------------------------------------------------
# 1. file_moves applied — dest files exist, source package files removed
# ---------------------------------------------------------------------------


def test_file_moves_applied(repo_copy, refactor_plan):
    result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)
    assert isinstance(result, ApplyResult)

    # At least one file move must have been applied
    file_move_actions = [a for a in result.applied if a.kind == "file_move"]
    assert len(file_move_actions) >= 1, (
        f"Expected at least 1 file_move action; escalations: {result.escalations}"
    )

    # Each destination should exist in repo_copy
    for fm in refactor_plan.file_moves:
        dest = repo_copy / fm.dest
        assert dest.exists(), (
            f"Expected destination {dest} to exist after apply_plan"
        )

    # Original messy_pkg/ .py files should no longer exist (rope moves them)
    original_pkg = repo_copy / "messy_pkg"
    remaining_py = list(original_pkg.glob("*.py")) if original_pkg.exists() else []
    assert remaining_py == [], (
        f"Expected messy_pkg/ to be empty after moves, but found: {remaining_py}"
    )


# ---------------------------------------------------------------------------
# 2. import rewrites — dest file imports from new pkg, not old messy_pkg
# ---------------------------------------------------------------------------


def test_import_rewrites_after_file_move(repo_copy, refactor_plan):
    apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

    # Find the mat.py destination (it imports from vec)
    mat_move = next(
        (fm for fm in refactor_plan.file_moves if Path(fm.src).name == "mat.py"),
        None,
    )
    assert mat_move is not None, "Expected a file move for mat.py"
    mat_dest = repo_copy / mat_move.dest
    assert mat_dest.exists(), f"mat.py destination {mat_dest} does not exist"

    content = mat_dest.read_text()
    # After rope rewrites, mat.py should NOT import from messy_pkg
    assert "messy_pkg" not in content, (
        f"Expected import from new package, not messy_pkg. mat.py content:\n{content}"
    )
    # Should import from some pkg_NNN
    assert "pkg_" in content or "from ." in content, (
        f"Expected import from pkg_NNN or relative import. mat.py content:\n{content}"
    )


# ---------------------------------------------------------------------------
# 3. approved symbol move — function lands in dest cluster's _unsorted.py
# ---------------------------------------------------------------------------


def test_approved_symbol_move(repo_copy, refactor_plan):
    # Find vec_from_pair() which should go to pkg_004 (vec's cluster)
    vec_from_pair = next(
        (sm for sm in refactor_plan.symbol_moves if "vec_from_pair" in sm.label),
        None,
    )
    if vec_from_pair is None:
        pytest.skip("vec_from_pair not in symbol_moves")

    # Approve the symbol
    vec_from_pair.approved = True

    try:
        result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        symbol_actions = [a for a in result.applied if a.kind == "symbol_move"]
        assert len(symbol_actions) >= 1, (
            f"Expected at least 1 symbol_move action; escalations: {result.escalations}"
        )

        # The _unsorted.py in the dest cluster should contain vec_from_pair
        dest_unsorted = repo_copy / vec_from_pair.dest_cluster / "_unsorted.py"
        assert dest_unsorted.exists(), (
            f"Expected {dest_unsorted} to exist after symbol move"
        )
        unsorted_content = dest_unsorted.read_text()
        assert "vec_from_pair" in unsorted_content, (
            f"Expected vec_from_pair in {dest_unsorted}:\n{unsorted_content}"
        )
    finally:
        # Reset approved flag to avoid polluting other tests
        vec_from_pair.approved = False


# ---------------------------------------------------------------------------
# 4. manifest count — applied length == file_moves + approved_symbols + organizes
# ---------------------------------------------------------------------------


def test_applied_count(repo_copy, refactor_plan):
    result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

    file_move_count = len([a for a in result.applied if a.kind == "file_move"])
    symbol_move_count = len([a for a in result.applied if a.kind == "symbol_move"])
    organize_count = len([a for a in result.applied if a.kind == "organize_imports"])

    assert file_move_count + symbol_move_count + organize_count == len(result.applied)

    # With only_approved_symbols=True and all approved=False, no symbol moves
    assert symbol_move_count == 0

    # File moves should match number of successful plan file_moves
    expected_file_moves = len(refactor_plan.file_moves)
    assert file_move_count == expected_file_moves, (
        f"Expected {expected_file_moves} file moves, got {file_move_count}; "
        f"escalations: {result.escalations}"
    )

    # Total applied actions = file moves + 0 symbol moves + organize passes
    total = len(result.applied)
    assert total >= file_move_count


# ---------------------------------------------------------------------------
# 5. rollback — original tree is restored
# ---------------------------------------------------------------------------


def test_rollback_restores_tree(repo_copy, refactor_plan):
    # Snapshot the original tree (files only, excluding .ropeproject)
    def tree_snapshot(root: Path) -> set[str]:
        return {
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and ".ropeproject" not in p.parts
        }

    before = tree_snapshot(repo_copy)

    result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)
    after_apply = tree_snapshot(repo_copy)

    # Confirm something changed
    assert after_apply != before, "Expected apply_plan to change the file tree"

    # Rollback
    rollback(repo_copy, len(result.applied))
    after_rollback = tree_snapshot(repo_copy)

    assert after_rollback == before, (
        f"Rollback did not restore tree.\n"
        f"Missing after rollback: {before - after_rollback}\n"
        f"Extra after rollback: {after_rollback - before}"
    )
