"""Tests for graph_bridge.py.

Covers path normalization strategies, helper functions, and smoke tests
against both the messy_repo fixture and the real unified-algebra codebase.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from refactor_plan.graph_bridge import (
    dotted_module,
    ensure_graph,
    normalize_source_files,
    repo_relative,
    source_package,
)

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"

UNIALG_SRC = Path("/home/scanbot/unified-algebra/src/unialg")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_graph(path: Path, source_files: list[str]) -> None:
    """Write a minimal graph.json with nodes for each source_file."""
    nodes = [
        {
            "id": f"node_{i}",
            "source_file": sf,
            "label": Path(sf).name,
            "community": 0,
        }
        for i, sf in enumerate(source_files)
    ]
    path.write_text(json.dumps({"nodes": nodes, "links": []}), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. test_normalize_fixture_paths
# ---------------------------------------------------------------------------

def test_normalize_fixture_paths(tmp_path):
    """Fixture graph.json has paths like tests/fixtures/messy_repo/messy_pkg/vec.py.

    When repo_root is a tmp_path copy of messy_repo, strategy 2 (suffix
    stripping) should strip the leading 'tests/fixtures/messy_repo/' prefix
    and resolve each path via the copy.
    """
    # Copy the messy_repo fixture tree into tmp_path
    messy_copy = tmp_path / "messy_repo"
    shutil.copytree(FIXTURE_REPO, messy_copy)

    graph_json = messy_copy / ".refactor_plan" / "graph.json"

    resolved = normalize_source_files(graph_json, messy_copy)

    # All 9 source files should resolve
    assert len(resolved) == 9

    # Every resolved path should exist and be under the copy
    for raw, abs_path in resolved.items():
        assert abs_path.is_file(), f"{raw!r} did not resolve to an existing file"
        assert str(abs_path).startswith(str(messy_copy.resolve())), (
            f"{abs_path} is not under {messy_copy}"
        )


# ---------------------------------------------------------------------------
# 2. test_normalize_root_level_module
# ---------------------------------------------------------------------------

def test_normalize_root_level_module(tmp_path):
    """A module at repo root resolves via strategy 1 (direct join)."""
    (tmp_path / "utils.py").write_text("# utils\n", encoding="utf-8")

    graph_json = tmp_path / "graph.json"
    _write_graph(graph_json, ["utils.py"])

    resolved = normalize_source_files(graph_json, tmp_path)

    assert len(resolved) == 1
    assert resolved["utils.py"] == (tmp_path / "utils.py").resolve()


# ---------------------------------------------------------------------------
# 3. test_normalize_src_layout
# ---------------------------------------------------------------------------

def test_normalize_src_layout(tmp_path):
    """A standard src/ layout resolves via strategy 1."""
    mod = tmp_path / "src" / "mylib" / "core.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("# core\n", encoding="utf-8")

    graph_json = tmp_path / "graph.json"
    _write_graph(graph_json, ["src/mylib/core.py"])

    resolved = normalize_source_files(graph_json, tmp_path)

    assert len(resolved) == 1
    assert resolved["src/mylib/core.py"] == mod.resolve()


# ---------------------------------------------------------------------------
# 4. test_normalize_nested_packages
# ---------------------------------------------------------------------------

def test_normalize_nested_packages(tmp_path):
    """Deeply nested package resolves correctly."""
    deep = tmp_path / "pkg" / "sub" / "deep" / "mod.py"
    deep.parent.mkdir(parents=True)
    for d in [
        tmp_path / "pkg",
        tmp_path / "pkg" / "sub",
        tmp_path / "pkg" / "sub" / "deep",
    ]:
        (d / "__init__.py").write_text("", encoding="utf-8")
    deep.write_text("# mod\n", encoding="utf-8")

    graph_json = tmp_path / "graph.json"
    _write_graph(graph_json, ["pkg/sub/deep/mod.py"])

    resolved = normalize_source_files(graph_json, tmp_path)

    assert len(resolved) == 1
    assert resolved["pkg/sub/deep/mod.py"] == deep.resolve()


# ---------------------------------------------------------------------------
# 5. test_normalize_unresolvable_raises
# ---------------------------------------------------------------------------

def test_normalize_unresolvable_raises(tmp_path):
    """An unresolvable source_file raises ValueError naming the path."""
    graph_json = tmp_path / "graph.json"
    _write_graph(graph_json, ["nonexistent/gone.py"])

    with pytest.raises(ValueError, match="nonexistent/gone.py"):
        normalize_source_files(graph_json, tmp_path)


# ---------------------------------------------------------------------------
# 6. test_repo_relative
# ---------------------------------------------------------------------------

def test_repo_relative(tmp_path):
    """repo_relative returns the correct posix string for various depths."""
    # Root level
    root_file = tmp_path / "utils.py"
    root_file.touch()
    assert repo_relative(root_file.resolve(), tmp_path) == "utils.py"

    # One level deep
    pkg = tmp_path / "mylib"
    pkg.mkdir()
    mod = pkg / "core.py"
    mod.touch()
    assert repo_relative(mod.resolve(), tmp_path) == "mylib/core.py"

    # Two levels deep
    sub = pkg / "sub"
    sub.mkdir()
    deep = sub / "mod.py"
    deep.touch()
    assert repo_relative(deep.resolve(), tmp_path) == "mylib/sub/mod.py"


# ---------------------------------------------------------------------------
# 7. test_source_package
# ---------------------------------------------------------------------------

def test_source_package(tmp_path):
    """source_package returns None for root-level, correct parent for nested."""
    # Root-level module: no parent package
    root_file = tmp_path / "utils.py"
    root_file.touch()
    assert source_package(root_file.resolve(), tmp_path) is None

    # One level: parent dir is the package
    pkg = tmp_path / "mylib"
    pkg.mkdir()
    mod = pkg / "core.py"
    mod.touch()
    assert source_package(mod.resolve(), tmp_path) == "mylib"

    # Two levels deep: immediate parent is the sub-package
    sub = pkg / "sub"
    sub.mkdir()
    deep = sub / "utils.py"
    deep.touch()
    assert source_package(deep.resolve(), tmp_path) == "sub"


# ---------------------------------------------------------------------------
# 8. test_dotted_module
# ---------------------------------------------------------------------------

def test_dotted_module(tmp_path):
    """dotted_module converts .py paths to dotted Python module names."""
    # Root-level
    root_file = tmp_path / "utils.py"
    root_file.touch()
    assert dotted_module(root_file.resolve(), tmp_path) == "utils"

    # Single package
    pkg = tmp_path / "mylib"
    pkg.mkdir()
    mod = pkg / "core.py"
    mod.touch()
    assert dotted_module(mod.resolve(), tmp_path) == "mylib.core"

    # Nested package
    sub = pkg / "sub"
    sub.mkdir()
    deep = sub / "mod.py"
    deep.touch()
    assert dotted_module(deep.resolve(), tmp_path) == "mylib.sub.mod"

    # __init__.py
    init = pkg / "__init__.py"
    init.touch()
    assert dotted_module(init.resolve(), tmp_path) == "mylib.__init__"


# ---------------------------------------------------------------------------
# 9. test_normalize_against_unified_algebra
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not UNIALG_SRC.is_dir(),
    reason="unified-algebra source not available at expected path",
)
def test_normalize_against_unified_algebra(tmp_path):
    """Smoke test against the real unified-algebra multi-package Python project.

    Copies unialg/ (excluding __pycache__ and legacy) to tmp_path/unialg,
    generates a minimal graph.json, then asserts all files resolve.
    """
    unialg_copy = tmp_path / "unialg"

    def _ignore(src: str, names: list[str]) -> list[str]:
        return [n for n in names if n in ("__pycache__", "legacy")]

    shutil.copytree(str(UNIALG_SRC), str(unialg_copy), ignore=_ignore)

    # Collect all .py files under the copy, relative to tmp_path
    py_files = [
        f.relative_to(tmp_path).as_posix()
        for f in sorted(unialg_copy.rglob("*.py"))
        if "__pycache__" not in f.parts and "legacy" not in f.parts
    ]
    assert py_files, "Expected at least some .py files from unialg"

    graph_json = tmp_path / "graph.json"
    _write_graph(graph_json, py_files)

    resolved = normalize_source_files(graph_json, tmp_path)

    assert len(resolved) == len(py_files), (
        f"Expected {len(py_files)} resolved paths, got {len(resolved)}"
    )
    for raw, abs_path in resolved.items():
        assert abs_path.is_file(), f"{raw!r} did not resolve to an existing file"


# ---------------------------------------------------------------------------
# 10. test_ensure_graph_generates_when_missing
# ---------------------------------------------------------------------------

def test_ensure_graph_generates_when_missing(tmp_path):
    """ensure_graph runs graphify extraction when .refactor_plan/graph.json is absent."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
    (pkg / "utils.py").write_text("def bar():\n    pass\n", encoding="utf-8")

    graph_path = tmp_path / ".refactor_plan" / "graph.json"
    assert not graph_path.exists()

    returned = ensure_graph(tmp_path)

    assert returned == graph_path
    assert graph_path.exists()

    data = json.loads(graph_path.read_text(encoding="utf-8"))
    assert len(data.get("nodes", [])) > 0
    assert "links" in data

    # Second call returns immediately without re-extracting
    returned_again = ensure_graph(tmp_path)
    assert returned_again == graph_path


# ---------------------------------------------------------------------------
# 11. test_ensure_graph_existing_not_overwritten
# ---------------------------------------------------------------------------

def test_ensure_graph_existing_not_overwritten(tmp_path):
    """ensure_graph does not regenerate a graph.json that already exists."""
    refactor_dir = tmp_path / ".refactor_plan"
    refactor_dir.mkdir(parents=True)
    graph_path = refactor_dir / "graph.json"
    original_content = '{"nodes": [], "links": []}'
    graph_path.write_text(original_content, encoding="utf-8")

    ensure_graph(tmp_path)

    assert graph_path.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# 12. test_ensure_graph_no_python_files
# ---------------------------------------------------------------------------

def test_ensure_graph_no_python_files(tmp_path):
    """ensure_graph raises FileNotFoundError when no Python files exist."""
    with pytest.raises(FileNotFoundError, match="No Python files found"):
        ensure_graph(tmp_path)
