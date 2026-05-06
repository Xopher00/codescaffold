"""Build a package-level DAG from a GraphSnapshot for contract generation."""

from __future__ import annotations

import tomllib
from pathlib import Path

import networkx as nx

from codescaffold.graphify import GraphSnapshot


def build_package_dag(
    snap: GraphSnapshot,
    src_root: str = "src",
    root_package: str | None = None,
) -> nx.DiGraph:
    """Build a directed package-level graph from a GraphSnapshot.

    Groups symbol nodes by their immediate subpackage (rootpkg.subpkg).
    When root_package is given, only nodes under that package are included,
    excluding tests, fixtures, and sibling packages in the same repo.
    Returns a DiGraph whose nodes are subpackage strings and edges are import
    relationships.
    """
    G = snap.graph
    node_to_pkg: dict[str, str] = {}
    for node in G.nodes():
        src = G.nodes[node].get("source_file", "")
        pkg = _file_to_subpackage(src, src_root)
        if pkg and (
            root_package is None
            or pkg.startswith(root_package + ".")
        ):
            node_to_pkg[node] = pkg

    dag: nx.DiGraph = nx.DiGraph()
    dag.add_nodes_from(set(node_to_pkg.values()))

    for u, v, data in G.edges(data=True):
        if data.get("relation") not in ("imports_from", "calls", "uses"):
            continue
        pu = node_to_pkg.get(u)
        pv = node_to_pkg.get(v)
        if pu and pv and pu != pv:
            dag.add_edge(pu, pv)

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
