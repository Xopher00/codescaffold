"""Bridge between graphify and codescaffold.

Generates graph.json via graphify's Part A (AST) extraction when missing,
and normalizes source_file paths for downstream consumers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import networkx as nx

import graphify.build as gbuild
import graphify.cluster as gcluster
import graphify.export as gexport
from graphify.extract import collect_files, extract

log = logging.getLogger(__name__)


def ensure_graph(repo_root: Path) -> Path:
    """Return the path to .refactor_plan/graph.json, generating it if missing.

    Runs graphify Part A extraction (AST-only, no LLM) on all Python files
    under repo_root, then builds, clusters, and saves the graph.
    """
    refactor_dir = repo_root / ".refactor_plan"
    graph_path = refactor_dir / "graph.json"

    if graph_path.exists():
        log.info("graph.json already exists at %s", graph_path)
        return graph_path

    log.info("No graph.json found — running graphify extraction on %s", repo_root)
    refactor_dir.mkdir(parents=True, exist_ok=True)

    # Collect Python files
    code_files = collect_files(repo_root)
    if not code_files:
        raise FileNotFoundError(f"No Python files found under {repo_root}")

    # Extract AST nodes and edges (Part A only — deterministic, no LLM)
    extraction = extract(code_files)
    node_count = len(extraction.get("nodes", []))
    edge_count = len(extraction.get("edges", []))
    log.info("Extracted %d nodes, %d edges from %d files", node_count, edge_count, len(code_files))

    # Build graph, cluster, save
    G = gbuild.build_from_json(extraction)
    communities = gcluster.cluster(G)
    gexport.to_json(G, communities, str(graph_path))

    log.info("Saved graph.json (%d nodes, %d edges, %d communities)",
             G.number_of_nodes(), G.number_of_edges(), len(communities))
    return graph_path


def normalize_source_files(
    graph_json_path: Path,
    repo_root: Path,
) -> dict[str, Path]:
    """Map every source_file in graph.json to its absolute on-disk path.

    Returns {graphify_source_file: absolute_path} for all unique non-empty
    source_file values found in graph nodes.

    Raises ValueError listing any source_file that cannot be resolved.
    """
    data = json.loads(graph_json_path.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])

    raw_paths: set[str] = set()
    for node in nodes:
        sf = node.get("source_file", "")
        if sf:
            raw_paths.add(sf)

    resolved: dict[str, Path] = {}
    unresolved: list[str] = []

    for raw in sorted(raw_paths):
        abs_path = _resolve_once(raw, repo_root)
        if abs_path is not None:
            resolved[raw] = abs_path
        else:
            unresolved.append(raw)

    if unresolved:
        raise ValueError(
            f"Cannot resolve {len(unresolved)} source_file path(s) under {repo_root}:\n"
            + "\n".join(f"  - {p}" for p in unresolved)
        )

    return resolved


def _resolve_once(raw: str, repo_root: Path) -> Path | None:
    """Try to resolve a single graphify source_file to an absolute path.

    Strategies (in order):
    1. repo_root / raw  (direct join — works when graphify ran from repo_root)
    2. Progressive suffix stripping — strip leading components of raw one at a
       time until a match is found under repo_root. This handles the case where
       graphify ran from a parent directory (e.g., raw = "sub/pkg/mod.py" but
       the file is at repo_root / "pkg/mod.py").
    """
    # Strategy 1: direct join
    direct = repo_root / raw
    if direct.is_file():
        return direct.resolve()

    # Strategy 2: strip leading path components
    parts = Path(raw).parts
    for start in range(1, len(parts)):
        candidate = repo_root.joinpath(*parts[start:])
        if candidate.is_file():
            return candidate.resolve()

    return None


def repo_relative(abs_path: Path, repo_root: Path) -> str:
    """Convert an absolute path to a repo-relative posix string."""
    return abs_path.relative_to(repo_root.resolve()).as_posix()


def source_package(abs_path: Path, repo_root: Path) -> str | None:
    """Return the immediate parent package name, or None for root-level modules."""
    rel = abs_path.relative_to(repo_root.resolve())
    parts = rel.parts
    if len(parts) >= 2:
        return parts[-2]
    return None


def dotted_module(abs_path: Path, repo_root: Path) -> str:
    """Convert an absolute .py path to a dotted Python module name."""
    rel = abs_path.relative_to(repo_root.resolve())
    return rel.with_suffix("").as_posix().replace("/", ".")
