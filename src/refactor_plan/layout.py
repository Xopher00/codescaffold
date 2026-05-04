"""Project layout detection — source root, cluster root, test paths.

Single source of truth consumed by the planner, validator, and graph bridge.
Detection reads config files in priority order and falls back to heuristics.
"""
from __future__ import annotations

import configparser
import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_TEST_HEURISTICS = ("tests", "test")


@dataclass
class ProjectLayout:
    source_root: Path
    cluster_root: Path
    test_roots: list[Path]
    has_tests: bool
    root_package: str


# ---------------------------------------------------------------------------
# Config readers
# ---------------------------------------------------------------------------

def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _pyproject_src_dirs(repo_root: Path) -> list[Path]:
    data = _read_toml(repo_root / "pyproject.toml")
    where: list[str] = (
        data.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
        .get("where", [])
    )
    return [repo_root / d for d in where if isinstance(d, str)]


def _pytest_testpaths(repo_root: Path) -> list[Path]:
    """Read testpaths from the first pytest config that declares them."""

    # 1. pyproject.toml [tool.pytest.ini_options]
    data = _read_toml(repo_root / "pyproject.toml")
    tp = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", [])
    if tp:
        return [repo_root / p for p in tp if isinstance(p, str)]

    # 2. pytest.ini [pytest]
    for name in ("pytest.ini",):
        ini = repo_root / name
        if ini.exists():
            cp = configparser.ConfigParser()
            cp.read(ini)
            raw = cp.get("pytest", "testpaths", fallback="").strip()
            if raw:
                return [repo_root / p for p in raw.split()]

    # 3. setup.cfg [tool:pytest]
    setup_cfg = repo_root / "setup.cfg"
    if setup_cfg.exists():
        cp = configparser.ConfigParser()
        cp.read(setup_cfg)
        raw = cp.get("tool:pytest", "testpaths", fallback="").strip()
        if raw:
            return [repo_root / p for p in raw.split()]

    # 4. tox.ini [pytest]
    tox_ini = repo_root / "tox.ini"
    if tox_ini.exists():
        cp = configparser.ConfigParser()
        cp.read(tox_ini)
        raw = cp.get("pytest", "testpaths", fallback="").strip()
        if raw:
            return [repo_root / p for p in raw.split()]

    return []


# ---------------------------------------------------------------------------
# Core detection helpers
# ---------------------------------------------------------------------------

_TEST_PARTS = {"tests", "test", "fixtures", "fixture", "conftest"}


def _is_test_file(path: Path) -> bool:
    if _TEST_PARTS.intersection(path.parts):
        return True
    stem = path.stem
    return stem.startswith("test_") or stem.endswith("_test") or stem == "conftest"


def _detect_source_root(repo_root: Path, source_files: list[str]) -> Path:
    explicit = _pyproject_src_dirs(repo_root)
    if explicit:
        return explicit[0]

    candidates = [repo_root / "src", repo_root / "lib", repo_root]
    filtered = [sf for sf in source_files if not _is_test_file(Path(sf))]
    sample = (filtered or source_files)[:20]
    for path_str in sample:
        p = Path(path_str)
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        for candidate in candidates:
            try:
                p.relative_to(candidate)
                return candidate
            except ValueError:
                continue
    return repo_root


def _detect_cluster_root(
    repo_root: Path,
    source_files: list[str],
    src_root: Path,
) -> Path:
    filtered_abs: list[Path] = []
    for sf in source_files:
        p = Path(sf)
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        if not _is_test_file(p) and p.name != "__init__.py":
            filtered_abs.append(p)

    if not filtered_abs:
        return src_root

    parent_dirs = [str(p.parent) for p in filtered_abs]
    try:
        common = Path(os.path.commonpath(parent_dirs))
    except ValueError:
        return src_root

    best_pkg: Path | None = None
    current = common
    while True:
        if (current / "__init__.py").exists():
            best_pkg = current
        if current == src_root or current == current.parent:
            break
        current = current.parent

    if best_pkg is not None:
        return best_pkg

    logger.debug("cluster_root fell back to src_root=%s", src_root)
    return src_root


def _detect_root_package(source_root: Path) -> str:
    """Return the name of the first top-level Python package under source_root."""
    try:
        for entry in sorted(source_root.iterdir()):
            if entry.is_dir() and (entry / "__init__.py").exists():
                return entry.name
    except OSError:
        pass
    return source_root.name


def _detect_test_roots(repo_root: Path) -> list[Path]:
    configured = _pytest_testpaths(repo_root)
    if configured:
        return [p for p in configured if p.exists()]
    # Heuristic
    found = []
    for name in _TEST_HEURISTICS:
        d = repo_root / name
        if d.is_dir():
            found.append(d)
    return found


def _has_tests(test_roots: list[Path]) -> bool:
    for root in test_roots:
        for pattern in ("test_*.py", "*_test.py"):
            if any(root.rglob(pattern)):
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_layout(
    repo_root: Path,
    source_files: list[str] | None = None,
) -> ProjectLayout:
    """Detect project layout from config files and heuristics.

    source_files is an optional list of known source paths (from the graph)
    used to improve source/cluster root detection.  When omitted, detection
    falls back to pyproject.toml and directory heuristics only.
    """
    files = source_files or []
    src_root = _detect_source_root(repo_root, files)
    cluster_root = _detect_cluster_root(repo_root, files, src_root)
    root_package = _detect_root_package(src_root)
    test_roots = _detect_test_roots(repo_root)
    has_tests = _has_tests(test_roots)

    return ProjectLayout(
        source_root=src_root,
        cluster_root=cluster_root,
        test_roots=test_roots,
        has_tests=has_tests,
        root_package=root_package,
    )
