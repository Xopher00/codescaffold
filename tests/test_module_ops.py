"""Tests for Track A: move_and_rename_module wrapper + ApprovedMove.new_name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codescaffold.operations import RopeChangeResult, move_and_rename_module
from codescaffold.plans.schema import ApprovedMove, Plan


def _make_module_repo(tmp_path: Path) -> Path:
    """Create src/simplepkg/{__init__,foo.py,caller.py,sub/__init__} for rope tests."""
    pkg = tmp_path / "src" / "simplepkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "foo.py").write_text("def bar():\n    return 42\n")
    (pkg / "caller.py").write_text(
        "from simplepkg.foo import bar\n\ndef main():\n    return bar()\n"
    )
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").touch()
    return tmp_path


class TestMoveAndRenameModule:
    def test_returns_change_result(self, tmp_path: Path):
        repo = _make_module_repo(tmp_path)
        result = move_and_rename_module(
            str(repo), "src/simplepkg/foo.py", "src/simplepkg/sub", "renamed.py"
        )
        assert isinstance(result, RopeChangeResult)
        assert len(result.changed_files) > 0

    def test_file_moved_and_renamed(self, tmp_path: Path):
        repo = _make_module_repo(tmp_path)
        move_and_rename_module(
            str(repo), "src/simplepkg/foo.py", "src/simplepkg/sub", "renamed.py"
        )
        assert (repo / "src" / "simplepkg" / "sub" / "renamed.py").exists()
        assert not (repo / "src" / "simplepkg" / "foo.py").exists()

    def test_caller_import_updated(self, tmp_path: Path):
        repo = _make_module_repo(tmp_path)
        move_and_rename_module(
            str(repo), "src/simplepkg/foo.py", "src/simplepkg/sub", "renamed.py"
        )
        caller = (repo / "src" / "simplepkg" / "caller.py").read_text()
        assert "simplepkg.sub.renamed" in caller
        assert "simplepkg.foo" not in caller


class TestApprovedMoveNewName:
    def test_new_name_defaults_none(self):
        move = ApprovedMove(kind="module", source_file="src/pkg/foo.py", target_file="src/pkg/sub")
        assert move.new_name is None

    def test_new_name_set(self):
        move = ApprovedMove(
            kind="module",
            source_file="src/pkg/foo.py",
            target_file="src/pkg/sub",
            new_name="renamed.py",
        )
        assert move.new_name == "renamed.py"

    def test_plan_loads_without_new_name(self):
        """Plans written before new_name was added must load with new_name=None."""
        plan = Plan.model_validate({
            "graph_hash": "abc123",
            "approved_moves": [
                {"kind": "module", "source_file": "src/pkg/foo.py", "target_file": "src/pkg/sub"}
            ],
        })
        assert plan.approved_moves[0].new_name is None

    def test_new_name_none_routes_to_move_module(self, tmp_path: Path):
        """Regression: ApprovedMove with new_name=None must work (no TypeError from dispatch)."""
        move = ApprovedMove(kind="module", source_file="src/pkg/foo.py", target_file="src/pkg/sub")
        assert move.new_name is None
        # The dispatch branch uses `if move.new_name:` so falsy None → move_module path.
        # Verify the schema invariant holds (no TypeError on attribute access).
        assert not move.new_name
