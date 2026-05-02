from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def messy_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "messy_pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "utils.py").write_text("def helper():\n    return 42\n")
    (src / "main.py").write_text(
        "from messy_pkg.utils import helper\n\n\ndef run():\n    return helper()\n"
    )
    return tmp_path


@pytest.fixture()
def messy_repo_with_large_func(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "messy_pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")

    large_body = "\n".join(f"    x_{i} = {i}" for i in range(50))
    god_py = (
        "def big_function():\n"
        f"{large_body}\n"
        "    return x_49\n"
        "\n\n"
        "def small_function():\n"
        "    return 1\n"
    )
    (src / "god.py").write_text(god_py)
    (src / "dest.py").write_text("")
    return tmp_path
