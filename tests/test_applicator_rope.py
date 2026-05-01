"""Tests for applicator/rope_runner.py.

Tests use a copy of the messy_repo fixture so original is never mutated.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import libcst as cst
from libcst.metadata import ByteSpanPositionProvider, MetadataWrapper
from rope.base import libutils
from rope.base.project import Project
from rope.refactor.move import MoveGlobal, create_move

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
    return plan(view, FIXTURE_REPO, FIXTURE_GRAPH)


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

    # Original messy_pkg/ .py files (except __init__.py) should no longer exist.
    # __init__.py is skipped by rope (A4) and copied via pathlib, so the original
    # stays in place. All other .py files should be gone.
    original_pkg = repo_copy / "messy_pkg"
    remaining_py = [
        p for p in (original_pkg.glob("*.py") if original_pkg.exists() else [])
        if p.name != "__init__.py"
    ]
    assert remaining_py == [], (
        f"Expected messy_pkg/ non-init files to be gone after moves, but found: {remaining_py}"
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

        # A3: symbol should land in dest_file, not _unsorted.py
        dest_file_path = repo_copy / vec_from_pair.dest_file
        assert dest_file_path.exists(), (
            f"Expected {dest_file_path} to exist after symbol move (dest_file={vec_from_pair.dest_file})"
        )
        dest_content = dest_file_path.read_text()
        assert "vec_from_pair" in dest_content, (
            f"Expected vec_from_pair in {dest_file_path}:\n{dest_content}"
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


# ---------------------------------------------------------------------------
# A7 — minimal repro: two symbols to the same dest file both land there
# ---------------------------------------------------------------------------


def _get_func_offset(code: str, name: str) -> int:
    """Get byte offset of a function name node using LibCST."""
    module = cst.parse_module(code)
    wrapper = MetadataWrapper(module)
    spans = wrapper.resolve(ByteSpanPositionProvider)
    for node in spans:
        if isinstance(node, cst.FunctionDef) and node.name.value == name:
            if node.name in spans:
                return spans[node.name].start
    return 0


def test_a7_two_symbols_same_dest_both_present(tmp_path):
    """A7 regression: moving two symbols to the same dest file must not lose the first.

    We re-resolve the dest resource before each create_move call (A7 fix) so
    rope sees the post-prior-move state. Both foo and bar must appear in c.py.
    """
    a_code = "def foo():\n    return 1\n"
    b_code = "def bar():\n    return 2\n"
    (tmp_path / "a.py").write_text(a_code)
    (tmp_path / "b.py").write_text(b_code)
    (tmp_path / "c.py").write_text('"""destination"""\n')

    foo_offset = _get_func_offset(a_code, "foo")
    bar_offset = _get_func_offset(b_code, "bar")

    project = Project(str(tmp_path))
    try:
        # Move foo → c.py
        a_res = libutils.path_to_resource(project, str(tmp_path / "a.py"))
        c_res = libutils.path_to_resource(project, str(tmp_path / "c.py"))
        assert a_res is not None and c_res is not None
        mover = create_move(project, a_res, foo_offset)
        assert isinstance(mover, MoveGlobal)
        changes = mover.get_changes(c_res)
        project.do(changes)

        # Re-resolve c_res before moving bar (A7 fix: rope must see updated state)
        b_res = libutils.path_to_resource(project, str(tmp_path / "b.py"))
        c_res2 = libutils.path_to_resource(project, str(tmp_path / "c.py"))
        assert b_res is not None and c_res2 is not None
        mover2 = create_move(project, b_res, bar_offset)
        assert isinstance(mover2, MoveGlobal)
        changes2 = mover2.get_changes(c_res2)
        project.do(changes2)
    finally:
        project.close()

    c_content = (tmp_path / "c.py").read_text()
    assert "foo" in c_content, f"foo missing from c.py after two moves:\n{c_content}"
    assert "bar" in c_content, f"bar missing from c.py after two moves:\n{c_content}"


# ---------------------------------------------------------------------------
# A3 + A7 fixture-level: approved symbol moves land in per-symbol dest_file
# ---------------------------------------------------------------------------


def test_approved_symbols_land_in_dest_file(repo_copy, refactor_plan):
    """A3+A7: After applying approved symbol moves, each symbol lands in dest_file.

    Approves vec_from_pair and distance, both targeting pkg_004/vec.py.
    Both must appear in that file — confirming A7 (no silent drop of first move).
    """
    vec_from_pair = next(
        (sm for sm in refactor_plan.symbol_moves if "vec_from_pair" in sm.label), None
    )
    distance = next(
        (sm for sm in refactor_plan.symbol_moves if sm.label == "distance()"), None
    )

    if vec_from_pair is None or distance is None:
        pytest.skip("vec_from_pair or distance not in symbol_moves")

    vec_from_pair.approved = True
    distance.approved = True

    try:
        result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        symbol_actions = [a for a in result.applied if a.kind == "symbol_move"]
        assert len(symbol_actions) >= 2, (
            f"Expected at least 2 symbol_move actions; escalations: {result.escalations}"
        )

        # Both symbols target pkg_004/vec.py — verify both are present
        vec_dest = repo_copy / "pkg_004" / "vec.py"
        assert vec_dest.exists(), f"Expected {vec_dest} to exist"
        content = vec_dest.read_text()
        assert "def vec_from_pair" in content, (
            f"vec_from_pair missing from pkg_004/vec.py:\n{content}"
        )
        assert "def distance" in content, (
            f"distance missing from pkg_004/vec.py:\n{content}"
        )
    finally:
        vec_from_pair.approved = False
        distance.approved = False


# ---------------------------------------------------------------------------
# A4 — No stray nested or top-level __init__.py after file moves
# ---------------------------------------------------------------------------


def test_no_stray_init_files_after_apply(repo_copy, refactor_plan):
    """A4: After file moves, there must be no nested pkg_NNN/messy_pkg/ subdir
    created by rope's MoveModule treating __init__.py as a package move.
    """
    apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

    # No nested pkg_NNN/messy_pkg/__init__.py (rope's MoveModule artifact for A4)
    for pkg_dir in repo_copy.glob("pkg_*"):
        nested = pkg_dir / "messy_pkg" / "__init__.py"
        assert not nested.exists(), (
            f"Stray nested __init__.py found: {nested} "
            "(A4 bug: rope created pkg_NNN/messy_pkg/ instead of using the flat dest dir)"
        )
        # Also check the dir itself
        nested_dir = pkg_dir / "messy_pkg"
        assert not nested_dir.exists(), (
            f"Stray nested messy_pkg/ dir found under {pkg_dir} (A4 bug not fixed)"
        )
