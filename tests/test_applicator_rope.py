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
    _is_residue,
    _ensure_future_annotations,
    _rewrite_cross_cluster_imports,
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
    stray_delete_count = len([a for a in result.applied if a.kind == "stray_delete"])
    import_rewrite_count = len([a for a in result.applied if a.kind == "import_rewrite"])

    # All action kinds should sum to total applied (F5 adds import_rewrite)
    assert (
        file_move_count + symbol_move_count + organize_count
        + stray_delete_count + import_rewrite_count
        == len(result.applied)
    )

    # With only_approved_symbols=True and all approved=False, no symbol moves
    assert symbol_move_count == 0

    # F1: each non-__init__.py file move emits 2 applied actions (MoveModule + Rename),
    # while __init__.py emits 1 (pathlib copy). So file_move_count >= plan.file_moves
    # and at most 2× the plan count.
    expected_file_moves = len(refactor_plan.file_moves)
    assert file_move_count >= expected_file_moves, (
        f"Expected at least {expected_file_moves} file move actions, got {file_move_count}; "
        f"escalations: {result.escalations}"
    )
    assert file_move_count <= expected_file_moves * 2, (
        f"Expected at most {expected_file_moves * 2} file move actions "
        f"(2 per file: move + rename), got {file_move_count}"
    )

    # F4: stray_delete actions should be present (source __init__ and/or top-level __init__)
    assert stray_delete_count >= 1, (
        f"Expected at least 1 stray_delete action (F4), got {stray_delete_count}"
    )

    # Total applied actions = file moves + 0 symbol moves + organize passes + stray deletes
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
    """A3+A7/F1: After applying approved symbol moves, each symbol lands in dest_file.

    Approves vec_from_pair and distance, both targeting pkg_004/mod_001.py
    (community 3 has only vec.py → mod_001.py under F1 placeholder naming).
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

        # Both symbols target pkg_004/mod_001.py (F1 placeholder name for vec.py)
        vec_dest = repo_copy / vec_from_pair.dest_file
        assert vec_dest.exists(), f"Expected {vec_dest} to exist"
        content = vec_dest.read_text()
        assert "def vec_from_pair" in content, (
            f"vec_from_pair missing from {vec_from_pair.dest_file}:\n{content}"
        )
        assert "def distance" in content, (
            f"distance missing from {vec_from_pair.dest_file}:\n{content}"
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


# ---------------------------------------------------------------------------
# F2 — Residue cleanup: files with only docstring/imports should be deleted
# ---------------------------------------------------------------------------


def test_residue_files_deleted_after_apply(repo_copy, refactor_plan):
    """F2: After symbol moves, residue files (only docstring + imports) are deleted.

    god.py moves to pkg_001/mod_003.py. Both vec_from_pair() and read_first_line()
    move out of it, leaving only the docstring + imports. After organize_imports,
    this file should be detected as residue and deleted.
    """
    # Approve all symbol moves to ensure god.py becomes empty
    for sm in refactor_plan.symbol_moves:
        sm.approved = True

    try:
        result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        # god.py was moved to pkg_001/mod_003.py
        god_dest = repo_copy / "pkg_001" / "mod_003.py"

        # After cleanup, the residue should be deleted
        assert not god_dest.exists(), (
            f"Expected residue {god_dest} to be deleted, but it still exists"
        )

        # Verify at least one residue_delete action was applied
        residue_actions = [a for a in result.applied if a.kind == "residue_delete"]
        assert len(residue_actions) >= 1, (
            f"Expected at least 1 residue_delete action, got {len(residue_actions)}"
        )
    finally:
        # Reset approved flags
        for sm in refactor_plan.symbol_moves:
            sm.approved = False


def test_residue_action_in_applied_list(repo_copy, refactor_plan):
    """F2: Residue deletions are recorded as AppliedAction with kind='residue_delete'."""
    # Approve all symbol moves
    for sm in refactor_plan.symbol_moves:
        sm.approved = True

    try:
        result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        # Find residue_delete actions
        residue_actions = [a for a in result.applied if a.kind == "residue_delete"]
        assert len(residue_actions) >= 1, (
            f"Expected at least 1 residue_delete action in applied list"
        )

        # Each residue_delete action should have a descriptive message
        for action in residue_actions:
            assert "residue" in action.description.lower(), (
                f"Expected 'residue' in description: {action.description}"
            )
            # history_index should be -1 (sentinel for non-rope actions)
            assert action.history_index == -1, (
                f"Expected history_index=-1 for residue_delete, got {action.history_index}"
            )
    finally:
        # Reset approved flags
        for sm in refactor_plan.symbol_moves:
            sm.approved = False


def test_non_residue_files_kept(repo_copy, refactor_plan):
    """F2: Non-residue files (with actual content) are kept even if they host symbol moves.

    vec.py moves to pkg_004/mod_001.py and hosts several symbols (Vec class, etc.).
    Even though symbols move out of it (or into it), it's not a residue (has the Vec class).
    This file must still exist after apply.
    """
    # Approve all symbol moves
    for sm in refactor_plan.symbol_moves:
        sm.approved = True

    try:
        result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        # vec.py moves to pkg_004/mod_001.py
        vec_dest = repo_copy / "pkg_004" / "mod_001.py"
        assert vec_dest.exists(), (
            f"Expected non-residue {vec_dest} to still exist after apply"
        )

        # mat.py moves to pkg_002/mod_002.py and contains Mat class
        mat_dest = repo_copy / "pkg_002" / "mod_002.py"
        assert mat_dest.exists(), (
            f"Expected non-residue {mat_dest} to still exist after apply"
        )

        # Verify that not all files were deleted (only residues)
        residue_actions = [a for a in result.applied if a.kind == "residue_delete"]
        file_move_count = len([a for a in result.applied if a.kind == "file_move"])
        assert file_move_count > len(residue_actions), (
            f"Expected fewer residue deletes than total file moves"
        )
    finally:
        # Reset approved flags
        for sm in refactor_plan.symbol_moves:
            sm.approved = False


# ---------------------------------------------------------------------------
# _is_residue unit tests
# ---------------------------------------------------------------------------


def test_is_residue_docstring_only(tmp_path):
    """_is_residue: file with only docstring → True."""
    code = '"""Module docstring."""\n'
    path = tmp_path / "residue1.py"
    path.write_text(code)
    assert _is_residue(path) is True


def test_is_residue_docstring_with_future_and_all(tmp_path):
    """_is_residue: docstring + __future__ + __all__ → True."""
    code = '''"""Module docstring."""
from __future__ import annotations

__all__ = ["some_export"]
'''
    path = tmp_path / "residue2.py"
    path.write_text(code)
    assert _is_residue(path) is True


def test_is_residue_with_function_def(tmp_path):
    """_is_residue: file with function def → False."""
    code = '''"""Module docstring."""

def foo():
    return 42
'''
    path = tmp_path / "not_residue1.py"
    path.write_text(code)
    assert _is_residue(path) is False


def test_is_residue_with_class_def(tmp_path):
    """_is_residue: file with class def → False."""
    code = '''"""Module docstring."""

class Bar:
    pass
'''
    path = tmp_path / "not_residue2.py"
    path.write_text(code)
    assert _is_residue(path) is False


# ---------------------------------------------------------------------------
# F3 — _ensure_future_annotations unit tests
# ---------------------------------------------------------------------------


def test_ensure_future_annotations_adds_to_empty_file(tmp_path):
    """F3: _ensure_future_annotations adds import to file with only class def."""
    code = '''class Vec:
    """2D vector."""
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y
'''
    path = tmp_path / "test_module.py"
    path.write_text(code)

    result = _ensure_future_annotations(path)
    assert result is True, "Expected file to be modified"

    modified = path.read_text()
    assert "from __future__ import annotations" in modified, (
        f"Expected future annotations import in modified file:\n{modified}"
    )
    # Import should be first line
    lines = modified.split("\n")
    assert "from __future__ import annotations" in lines[0], (
        f"Expected future import as first line, got: {lines[0]}"
    )


def test_ensure_future_annotations_after_docstring(tmp_path):
    """F3: _ensure_future_annotations places import after module docstring."""
    code = '''"""Module docstring."""

class Vec:
    """2D vector."""
    pass
'''
    path = tmp_path / "test_with_doc.py"
    path.write_text(code)

    result = _ensure_future_annotations(path)
    assert result is True, "Expected file to be modified"

    modified = path.read_text()
    lines = modified.split("\n")
    # Line 0 should still be the docstring
    assert '"""Module docstring."""' in lines[0], (
        f"Expected docstring as first line, got: {lines[0]}"
    )
    # Line 1 or 2 should be the future import (possibly blank line in between)
    import_found_idx = None
    for i in range(1, min(4, len(lines))):
        if "from __future__ import annotations" in lines[i]:
            import_found_idx = i
            break
    assert import_found_idx is not None, (
        f"Expected future import after docstring, got:\n{modified}"
    )


def test_ensure_future_annotations_idempotent(tmp_path):
    """F3: _ensure_future_annotations is idempotent (no-op if already present)."""
    code = '''from __future__ import annotations

class Vec:
    """2D vector."""
    pass
'''
    path = tmp_path / "already_has_import.py"
    path.write_text(code)

    result = _ensure_future_annotations(path)
    assert result is False, "Expected no modification when import already present"

    # Verify content unchanged
    assert path.read_text() == code, "Expected file to be unchanged"


def test_ensure_future_annotations_other_future_import(tmp_path):
    """F3: _ensure_future_annotations adds annotations import if only other __future__ imports exist."""
    code = '''from __future__ import division

class Vec:
    def __init__(self, x: float, y: float):
        self.x = x / 1.0
'''
    path = tmp_path / "other_future.py"
    path.write_text(code)

    result = _ensure_future_annotations(path)
    assert result is True, "Expected file to be modified"

    modified = path.read_text()
    assert "from __future__ import annotations" in modified, (
        f"Expected annotations import added, got:\n{modified}"
    )
    # Should have both division and annotations imports (either on same line or separate)
    assert "division" in modified and "annotations" in modified, (
        f"Expected both division and annotations, got:\n{modified}"
    )


# ---------------------------------------------------------------------------
# F3 — Integration: destination files are importable after apply
# ---------------------------------------------------------------------------


def test_destination_files_importable_after_apply(repo_copy, refactor_plan):
    """F3 integration: After apply, moved symbols are importable without NameError.

    This is the regression test for the forward-ref NameError: moved symbols
    whose type annotations reference classes defined later should not fail
    on import thanks to `from __future__ import annotations`.
    """
    import subprocess
    import sys

    # Approve all symbol moves
    for sm in refactor_plan.symbol_moves:
        sm.approved = True

    try:
        result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        # Verify at least some symbol moves were applied
        symbol_actions = [a for a in result.applied if a.kind == "symbol_move"]
        assert len(symbol_actions) >= 1, (
            f"Expected at least 1 symbol_move action; got {len(symbol_actions)}"
        )

        # For each unique dest_file with symbol moves, verify the module is importable
        unique_dests = set(sm.dest_file for sm in refactor_plan.symbol_moves if sm.approved)
        for dest_file in unique_dests:
            # Convert dest_file (e.g., "pkg_004/mod_001.py") to import form
            dest_path = Path(dest_file)
            pkg_name = dest_path.parent.name
            mod_name = dest_path.stem

            # Run python -c "import pkg_NNN.mod_MMM" in the repo_copy
            cmd = [sys.executable, "-c", f"import {pkg_name}.{mod_name}"]
            result_proc = subprocess.run(
                cmd,
                cwd=repo_copy,
                capture_output=True,
                text=True,
            )

            assert result_proc.returncode == 0, (
                f"Failed to import {pkg_name}.{mod_name} after apply; stderr:\n{result_proc.stderr}"
            )
    finally:
        # Reset approved flags
        for sm in refactor_plan.symbol_moves:
            sm.approved = False


# ---------------------------------------------------------------------------
# F4 — Stray __init__.py cleanup
# ---------------------------------------------------------------------------


def test_no_top_level_init_after_apply(repo_copy, refactor_plan):
    """F4: After apply, there must be no stray top-level __init__.py at repo root.

    A top-level __init__.py at the repo root is never legitimate for a Python
    package. If rope's MoveModule or other operations create one, it must be deleted.
    """
    apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

    top_init = repo_copy / "__init__.py"
    assert not top_init.exists(), (
        f"Stray top-level __init__.py found at {top_init} "
        "(F4 bug: top-level __init__.py should have been deleted)"
    )


def test_no_source_messy_pkg_init_after_apply(repo_copy, refactor_plan):
    """F4: After apply, source messy_pkg/__init__.py must be deleted.

    The pathlib-copy fix for __init__.py (option a, A4) copies the content to
    the placeholder dest but must also delete the source __init__.py.
    """
    apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

    source_init = repo_copy / "messy_pkg" / "__init__.py"
    assert not source_init.exists(), (
        f"Stray source __init__.py found at {source_init} "
        "(F4 bug: source __init__.py should have been unlinked after copy)"
    )


def test_stray_delete_actions_recorded(repo_copy, refactor_plan):
    """F4: Stray __init__.py deletions are recorded as AppliedAction with kind='stray_delete'."""
    result = apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

    # Should have at least one stray_delete action (top-level or source package init)
    stray_actions = [a for a in result.applied if a.kind == "stray_delete"]
    assert len(stray_actions) >= 1, (
        f"Expected at least 1 stray_delete action in applied list, got {len(stray_actions)}"
    )

    # Each stray_delete action should have a descriptive message
    for action in stray_actions:
        assert "init" in action.description.lower(), (
            f"Expected 'init' in description for stray_delete: {action.description}"
        )
        # history_index should be -1 (sentinel for non-rope actions)
        assert action.history_index == -1, (
            f"Expected history_index=-1 for stray_delete, got {action.history_index}"
        )


# ---------------------------------------------------------------------------
# F5 — Cross-cluster import rewrite post-pass
# ---------------------------------------------------------------------------


def test_cross_cluster_imports_rewritten_to_absolute(repo_copy, refactor_plan):
    """F5: After apply, cross-cluster relative imports are rewritten to absolute.

    pkg_003/mod_001.py (was reader.py) imports Vec and distance from sibling
    modules that ended up in different placeholder packages.  Both must be
    rewritten to absolute imports pointing at their new locations.
    """
    # Approve all symbol moves so distance() ends up in pkg_004/mod_001.py
    for sm in refactor_plan.symbol_moves:
        sm.approved = True

    try:
        apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        reader_dest = repo_copy / "pkg_003" / "mod_001.py"
        assert reader_dest.exists(), f"Expected {reader_dest} to exist"
        content = reader_dest.read_text()

        # The broken relative import must be gone
        assert "from .vec" not in content, (
            f"Expected 'from .vec' to be rewritten; pkg_003/mod_001.py:\n{content}"
        )
        assert "from .geom" not in content, (
            f"Expected 'from .geom' to be rewritten; pkg_003/mod_001.py:\n{content}"
        )

        # Vec lives in pkg_004/mod_001.py — absolute import expected
        assert "from pkg_004.mod_001 import" in content and "Vec" in content, (
            f"Expected absolute import of Vec from pkg_004.mod_001; content:\n{content}"
        )
    finally:
        for sm in refactor_plan.symbol_moves:
            sm.approved = False


def test_intra_cluster_relative_imports_preserved(repo_copy, refactor_plan):
    """F5: Intra-cluster relative imports (e.g. from .mod_002 import Mat) are kept.

    pkg_002/mod_001.py (was geom.py) imports Mat from mat.py which also moved
    into pkg_002 as mod_002.py.  That relative import is valid and must be kept.
    """
    for sm in refactor_plan.symbol_moves:
        sm.approved = True

    try:
        apply_plan(refactor_plan, repo_copy, only_approved_symbols=True)

        geom_dest = repo_copy / "pkg_002" / "mod_001.py"
        assert geom_dest.exists(), f"Expected {geom_dest} to exist"
        content = geom_dest.read_text()

        # Mat is in the same cluster (pkg_002/mod_002.py) — relative import preserved
        assert "from .mod_002 import Mat" in content, (
            f"Expected intra-cluster relative import 'from .mod_002 import Mat' "
            f"to be preserved; pkg_002/mod_001.py:\n{content}"
        )
    finally:
        for sm in refactor_plan.symbol_moves:
            sm.approved = False


def test_rewrite_cross_cluster_imports_idempotent(tmp_path):
    """F5 unit: _rewrite_cross_cluster_imports is idempotent — second call returns False.

    Build a synthetic dest file and src_to_dest map; call the helper twice;
    verify the second call returns False (no changes).
    """
    # Create a fake package structure
    pkg_a = tmp_path / "pkg_a"
    pkg_b = tmp_path / "pkg_b"
    pkg_a.mkdir()
    pkg_b.mkdir()
    (pkg_a / "__init__.py").touch()
    (pkg_b / "__init__.py").touch()

    # pkg_a/mod_001.py: formerly "old_pkg/foo.py", imports from "old_pkg/bar.py"
    # which is now "pkg_b/mod_001.py"
    dest_file = pkg_a / "mod_001.py"
    dest_file.write_text(
        '"""Module a."""\nfrom __future__ import annotations\n\nfrom .bar import something\n',
        encoding="utf-8",
    )

    src_to_dest = {
        "old_pkg/foo.py": "pkg_a/mod_001.py",
        "old_pkg/bar.py": "pkg_b/mod_001.py",
    }

    # First call: should rewrite `from .bar import something` → absolute
    changed_first = _rewrite_cross_cluster_imports(dest_file, tmp_path, src_to_dest)
    assert changed_first is True, "Expected first call to modify the file"

    content_after_first = dest_file.read_text()
    assert "from .bar" not in content_after_first, (
        f"Expected relative import to be rewritten; content:\n{content_after_first}"
    )
    assert "pkg_b" in content_after_first, (
        f"Expected absolute import with pkg_b; content:\n{content_after_first}"
    )

    # Second call: file is already correct — no changes expected
    changed_second = _rewrite_cross_cluster_imports(dest_file, tmp_path, src_to_dest)
    assert changed_second is False, (
        f"Expected second call to be a no-op; content after second call:\n{dest_file.read_text()}"
    )
