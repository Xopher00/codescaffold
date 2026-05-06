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

    Also handles graphify package-reference nodes (e.g. 'codescaffold_sandbox')
    that are created for cross-package imports and carry no source_file attribute.
    Returns a DiGraph whose nodes are subpackage strings and edges are import
    relationships.
    """
    G = snap.graph
    node_to_pkg: dict[str, str] = {}
    for node in G.nodes():
        src = G.nodes[node].get("source_file", "")
        pkg = _file_to_subpackage(src, src_root)
        if not pkg and root_package:
            pkg = _pkg_ref_node_to_package(node, root_package)
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


def _pkg_ref_node_to_package(node_id: str, root_package: str) -> str | None:
    """Map a graphify package-reference node to its dotted package name.

    Graphify creates nodes like 'codescaffold_sandbox' for cross-package imports
    whose target is a package, not a source file. These nodes have no source_file
    attribute. Recognise the pattern '{root}_{subpkg}' and convert to dotted form.
    Only single-component subpackage names are matched to avoid false positives
    on deeper node IDs like 'codescaffold_mcp_tools'.
    """
    prefix = root_package + "_"
    if node_id.startswith(prefix):
        sub = node_id[len(prefix):]
        if sub and "_" not in sub:
            return f"{root_package}.{sub}"
    return None


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
