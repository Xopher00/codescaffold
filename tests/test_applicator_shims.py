"""Tests for applicator/shims.py (T7).

Fixture: tests/fixtures/messy_repo  (same graph.json used by planner tests)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from refactor_plan.interface.cluster_view import build_view
from refactor_plan.planning.planner import plan
from refactor_plan.planning.shims import detect_external_access, emit_shims

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def refactor_plan():
    view = build_view(FIXTURE_GRAPH)
    return plan(view, FIXTURE_REPO)


@pytest.fixture
def repo_copy(tmp_path):
    """Fresh copy of messy_repo for each test that writes files."""
    src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".refactor_plan", "graphify-out"))
    return dst


# ---------------------------------------------------------------------------
# 1. mode="never" returns [] and creates no files
# ---------------------------------------------------------------------------

def test_never_returns_empty(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="never")
    assert result == []


def test_never_creates_no_files(refactor_plan, repo_copy):
    # Collect all py files before.
    before = set(repo_copy.rglob("*.py"))
    emit_shims(refactor_plan, repo_copy, mode="never")
    after = set(repo_copy.rglob("*.py"))
    assert before == after


# ---------------------------------------------------------------------------
# 2. mode="always" creates one shim per file_move; content starts correctly
# ---------------------------------------------------------------------------

def test_always_creates_one_shim_per_file_move(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="always")
    assert len(result) == len(refactor_plan.file_moves)


def test_always_shim_content_starts_with_from_pkg(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="always")
    assert len(result) >= 1, "Expected at least one file_move in fixture"
    for shim_path in result:
        text = shim_path.read_text(encoding="utf-8")
        # Must start with 'from pkg_NNN.<basename> import *'
        lines = text.splitlines()
        assert len(lines) >= 2
        import_line = lines[1]
        assert import_line.startswith("from pkg_"), (
            f"Expected 'from pkg_NNN...' but got: {import_line!r}"
        )
        assert "import *" in import_line


def test_always_shim_paths_match_file_move_srcs(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="always")
    expected_paths = {repo_copy / fm.src for fm in refactor_plan.file_moves}
    assert set(result) == expected_paths


# ---------------------------------------------------------------------------
# 3. mode="auto" on the fixture — >=0 shims; if any, content is correct
# ---------------------------------------------------------------------------

def test_auto_returns_list(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="auto")
    assert isinstance(result, list)


def test_auto_shims_have_correct_content(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="auto")
    for shim_path in result:
        text = shim_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) == 3, f"Shim should be 3 lines, got {len(lines)}: {text!r}"
        assert lines[0].startswith("# auto-generated compat shim")
        assert lines[1].startswith("from pkg_")
        assert "import *" in lines[1]
        assert "__deprecated_path__ = True" in lines[2]


def test_auto_does_not_exceed_file_move_count(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="auto")
    # Can't produce more shims than there are file moves.
    assert len(result) <= len(refactor_plan.file_moves)


# ---------------------------------------------------------------------------
# 4. detect_external_access
# ---------------------------------------------------------------------------

def test_detect_external_access_true(tmp_path):
    """pkg_b/bar.py imports from pkg_a.foo → True."""
    pkg_a = tmp_path / "pkg_a"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("")
    foo = pkg_a / "foo.py"
    foo.write_text("# empty\n")

    pkg_b = tmp_path / "pkg_b"
    pkg_b.mkdir()
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "bar.py").write_text("from pkg_a.foo import *\n")

    assert detect_external_access(foo, tmp_path) is True


def test_detect_external_access_false(tmp_path):
    """pkg_b/bar.py does NOT import from pkg_a.foo → False."""
    pkg_a = tmp_path / "pkg_a"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("")
    foo = pkg_a / "foo.py"
    foo.write_text("# empty\n")

    pkg_b = tmp_path / "pkg_b"
    pkg_b.mkdir()
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "bar.py").write_text("# nothing here\n")

    assert detect_external_access(foo, tmp_path) is False


def test_detect_external_access_import_style(tmp_path):
    """Plain 'import pkg_a.foo' also triggers True."""
    pkg_a = tmp_path / "pkg_a"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("")
    foo = pkg_a / "foo.py"
    foo.write_text("# empty\n")

    pkg_b = tmp_path / "pkg_b"
    pkg_b.mkdir()
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "bar.py").write_text("import pkg_a.foo\n")

    assert detect_external_access(foo, tmp_path) is True


def test_detect_external_access_sibling_excluded(tmp_path):
    """Files inside src_file's own package are not considered external."""
    pkg_a = tmp_path / "pkg_a"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("")
    foo = pkg_a / "foo.py"
    foo.write_text("# empty\n")
    # sibling imports within the same package — should NOT trigger
    (pkg_a / "baz.py").write_text("from pkg_a.foo import something\n")

    assert detect_external_access(foo, tmp_path) is False


# ---------------------------------------------------------------------------
# 5. Round-trip: written shim contains __deprecated_path__ = True
# ---------------------------------------------------------------------------

def test_shim_contains_deprecated_path_flag(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="always")
    assert len(result) >= 1, "Need at least one file_move for this test"
    for shim_path in result:
        text = shim_path.read_text(encoding="utf-8")
        assert "__deprecated_path__ = True" in text


def test_shim_has_exactly_three_lines(refactor_plan, repo_copy):
    result = emit_shims(refactor_plan, repo_copy, mode="always")
    for shim_path in result:
        lines = shim_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3, (
            f"Shim at {shim_path} should have 3 lines, got {len(lines)}"
        )
