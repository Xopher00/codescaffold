"""Tests for codescaffold.operations — the typed Rope wrapper layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from codescaffold.operations import (
    RopeArgumentError,
    RopeChangeResult,
    RopeRefactoringError,
    SymbolInfo,
    close_rope_project,
    list_symbols,
    move_module,
    move_symbol,
    rename_symbol,
)


# ---------------------------------------------------------------------------
# list_symbols
# ---------------------------------------------------------------------------

class TestListSymbols:
    def test_returns_typed_symbol_infos(self, messy_repo: Path):
        syms = list_symbols(str(messy_repo), "src/messy_pkg/utils.py")
        assert isinstance(syms, list)
        assert all(isinstance(s, SymbolInfo) for s in syms)

    def test_finds_helper_function(self, messy_repo: Path):
        syms = list_symbols(str(messy_repo), "src/messy_pkg/utils.py")
        names = [s.name for s in syms]
        assert "helper" in names

    def test_bad_file_raises_argument_error(self, messy_repo: Path):
        with pytest.raises(RopeArgumentError):
            list_symbols(str(messy_repo), "nonexistent.py")


# ---------------------------------------------------------------------------
# move_symbol
# ---------------------------------------------------------------------------

class TestMoveSymbol:
    def test_returns_change_result(self, messy_repo: Path):
        dest = messy_repo / "src" / "messy_pkg" / "dest.py"
        dest.write_text("")
        result = move_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "src/messy_pkg/dest.py",
        )
        assert isinstance(result, RopeChangeResult)
        assert len(result.changed_files) > 0

    def test_symbol_moved_to_dest(self, messy_repo: Path):
        dest = messy_repo / "src" / "messy_pkg" / "dest.py"
        dest.write_text("")
        move_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "src/messy_pkg/dest.py",
        )
        content = dest.read_text()
        assert "def helper" in content

    def test_unknown_symbol_raises(self, messy_repo: Path):
        dest = messy_repo / "src" / "messy_pkg" / "dest.py"
        dest.write_text("")
        with pytest.raises(RopeArgumentError):
            move_symbol(
                str(messy_repo),
                "src/messy_pkg/utils.py",
                "nonexistent_symbol",
                "src/messy_pkg/dest.py",
            )


# ---------------------------------------------------------------------------
# rename_symbol
# ---------------------------------------------------------------------------

class TestRenameSymbol:
    def test_returns_change_result(self, messy_repo: Path):
        result = rename_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "helper_renamed",
        )
        assert isinstance(result, RopeChangeResult)
        assert len(result.changed_files) > 0

    def test_symbol_renamed_in_file(self, messy_repo: Path):
        rename_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "helper_renamed",
        )
        content = (messy_repo / "src" / "messy_pkg" / "utils.py").read_text()
        assert "def helper_renamed" in content

    def test_import_updated_in_caller(self, messy_repo: Path):
        rename_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "helper_renamed",
        )
        content = (messy_repo / "src" / "messy_pkg" / "main.py").read_text()
        assert "helper_renamed" in content
        assert "helper" not in content.replace("helper_renamed", "")


# ---------------------------------------------------------------------------
# move_module
# ---------------------------------------------------------------------------

class TestMoveModule:
    def test_returns_change_result(self, messy_repo: Path):
        dest_dir = messy_repo / "src" / "messy_pkg" / "sub"
        dest_dir.mkdir()
        (dest_dir / "__init__.py").write_text("")
        result = move_module(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "src/messy_pkg/sub",
        )
        assert isinstance(result, RopeChangeResult)

    def test_module_moved_to_dest(self, messy_repo: Path):
        dest_dir = messy_repo / "src" / "messy_pkg" / "sub"
        dest_dir.mkdir()
        (dest_dir / "__init__.py").write_text("")
        move_module(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "src/messy_pkg/sub",
        )
        assert (dest_dir / "utils.py").exists()


# ---------------------------------------------------------------------------
# close_rope_project
# ---------------------------------------------------------------------------

class TestCloseRopeProject:
    def test_no_exception_on_known_project(self, messy_repo: Path):
        list_symbols(str(messy_repo), "src/messy_pkg/utils.py")
        close_rope_project(str(messy_repo))  # should not raise

    def test_no_exception_on_unknown_project(self, tmp_path: Path):
        close_rope_project(str(tmp_path))  # nothing cached, should not raise


# ---------------------------------------------------------------------------
# Result type invariants
# ---------------------------------------------------------------------------

class TestRopeChangeResult:
    def test_immutable(self, messy_repo: Path):
        result = rename_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "helper2",
        )
        with pytest.raises((AttributeError, TypeError)):
            result.changed_files = ()  # type: ignore[misc]

    def test_changed_files_is_tuple(self, messy_repo: Path):
        result = rename_symbol(
            str(messy_repo),
            "src/messy_pkg/utils.py",
            "helper",
            "helper3",
        )
        assert isinstance(result.changed_files, tuple)
