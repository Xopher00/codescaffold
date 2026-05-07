"""Build a package-level DAG for contract generation, using grimp."""

from __future__ import annotations

import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import importlib
import importlib.util

import grimp
import networkx as nx


@contextmanager
def _prepend_syspath(path: Path) -> Generator[None, None, None]:
    p = str(path.resolve())
    sys.path.insert(0, p)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        try:
            sys.path.remove(p)
        except ValueError:
            pass
        importlib.invalidate_caches()


def build_package_dag(
    repo_path: Path,
    root_package: str | None = None,
    src_root: str = "src",
) -> nx.DiGraph:
    """Build a directed package-level DAG using grimp.

    Nodes are squashed to at most two levels: 'rootpkg' and 'rootpkg.subpkg'.
    Edges represent direct imports between packages. grimp is the same engine
    import-linter uses internally, so this DAG and lint-imports results are
    guaranteed consistent.
    """
    repo_path = Path(repo_path).resolve()
    if root_package is None:
        root_package = detect_root_package(repo_path)

    src_dir = repo_path / src_root
    syspath_target = src_dir if src_dir.is_dir() else repo_path
    _modules_before = set(sys.modules)
    with _prepend_syspath(syspath_target):
        g = grimp.build_graph(root_package, include_external_packages=False)

    # Purge only modules added by this grimp call (i.e. from the tmp_path fixture).
    # Modules already in sys.modules before the call (e.g. codescaffold itself) are
    # left intact so that mock patches on their objects remain valid.
    for key in [
        k for k in sys.modules
        if k not in _modules_before
        and (k == root_package or k.startswith(root_package + "."))
    ]:
        del sys.modules[key]

    prefix = root_package + "."

    def _squash(modname: str) -> str | None:
        if modname == root_package:
            return root_package
        if not modname.startswith(prefix):
            return None
        sub = modname[len(prefix):].split(".", 1)[0]
        return f"{root_package}.{sub}"

    dag: nx.DiGraph = nx.DiGraph()
    for mod in g.modules:
        sq = _squash(mod)
        if sq:
            dag.add_node(sq)

    for mod in g.modules:
        u = _squash(mod)
        if u is None:
            continue
        for imported in g.find_modules_directly_imported_by(mod):
            v = _squash(imported)
            if v and v != u:
                dag.add_edge(u, v)

    return dag


def _file_to_subpackage(source_file: str, src_root: str = "src") -> str | None:
    """Convert a file path to its subpackage identifier (rootpkg.subpkg).

    src/codescaffold/graphify/extract.py  → codescaffold.graphify
    src/codescaffold/__init__.py          → codescaffold (root package only)
    """
    parts = Path(source_file).parts
    try:
        idx = list(parts).index(src_root)
    except ValueError:
        # Try without src/ prefix (flat layout)
        if len(parts) >= 2 and parts[0] not in (".", ".."):
            return ".".join(parts[:2])
        return None

    pkg_parts = parts[idx + 1:]
    if not pkg_parts:
        return None
    if len(pkg_parts) == 1:
        # File is directly in src/ — treat as root package (strip .py)
        return pkg_parts[0].replace(".py", "")
    # rootpkg.subpkg (first two levels after src/)
    root = pkg_parts[0]
    sub = pkg_parts[1]
    if sub.endswith(".py"):
        # File is directly in rootpkg/
        return root
    return f"{root}.{sub}"


def detect_root_package(repo_path: Path) -> str:
    """Return the importable root package name for the given repo.

    Tries pyproject.toml [tool.setuptools.packages.find] where = ["src"],
    then falls back to the first directory under src/.
    """
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        try:
            where = data["tool"]["setuptools"]["packages"]["find"]["where"]
            if "src" in where:
                src_dir = repo_path / "src"
                candidates = [
                    p.name for p in src_dir.iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                    and (p / "__init__.py").exists()
                ]
                if len(candidates) == 1:
                    return candidates[0]
        except (KeyError, TypeError):
            pass

    # Fallback: first package directory under src/
    src_dir = repo_path / "src"
    if src_dir.exists():
        candidates = [
            p.name for p in sorted(src_dir.iterdir())
            if p.is_dir() and (p / "__init__.py").exists()
        ]
        if candidates:
            return candidates[0]

    raise ValueError(f"Could not detect root package in {repo_path}")
