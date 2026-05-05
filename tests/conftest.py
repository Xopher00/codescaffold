"""Shared fixtures for codescaffold tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def messy_repo(tmp_path: Path) -> Path:
    """A minimal Python package with a misplaced helper function.

    Layout:
      src/messy_pkg/__init__.py
      src/messy_pkg/utils.py   — defines helper()
      src/messy_pkg/main.py    — imports and calls helper()

    Rope project root is tmp_path.
    """
    pkg = tmp_path / "src" / "messy_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "utils.py").write_text("def helper():\n    return 42\n")
    (pkg / "main.py").write_text(
        "from messy_pkg.utils import helper\n\n\ndef run():\n    return helper()\n"
    )
    return tmp_path


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A minimal git repo — needed by sandbox tests."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path
