from __future__ import annotations

import json
import logging
from pathlib import Path

import networkx as nx
from networkx.readwrite import node_link_graph

from graphify.extract import collect_files, extract
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.export import to_json
from refactor_plan.layout import detect_layout
from refactor_plan.execution import FileRef

logger = logging.getLogger(__name__)


def ensure_graph(repo_root: Path) -> Path:
    out_path = repo_root / "graphify-out" / "graph.json"
    needs_rebuild = True
    if out_path.exists():
        graph_mtime = out_path.stat().st_mtime
        newest_py = max(
            (p.stat().st_mtime for p in repo_root.rglob("*.py")),
            default=0.0,
        )
        needs_rebuild = newest_py > graph_mtime

    if not needs_rebuild:
        return out_path

    logger.info("Rebuilding graph from %s …", repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    paths = collect_files(repo_root)
    extraction = extract(paths, cache_root=repo_root)
    G = build_from_json(extraction)
    communities = cluster(G)
    to_json(G, communities, str(out_path))
    return out_path


def load_graph(graph_json: Path) -> tuple[nx.Graph, dict[int, list[str]]]:
    raw = json.loads(graph_json.read_text())

    # node_link_data may use "links" instead of "edges" depending on nx version
    if "links" in raw and "edges" not in raw:
        raw["edges"] = raw.pop("links")

    G = node_link_graph(raw, directed=True, multigraph=False)

    communities: dict[int, list[str]] = {}
    for node_id, attrs in G.nodes(data=True):
        comm = attrs.get("community")
        if comm is not None:
            communities.setdefault(int(comm), []).append(node_id)

    return G, communities


def normalize_source_files(G: nx.Graph, repo_root: Path) -> dict[str, Path]:
    seen: dict[str, Path] = {}
    for _node, attrs in G.nodes(data=True):
        sf = attrs.get("source_file")
        if not sf or sf in seen:
            continue
        p = Path(sf)
        if p.exists():
            seen[sf] = p
    return seen


def build_file_refs(G: nx.Graph, repo_root: Path) -> dict[str, FileRef]:
    """Resolve all file-node entries in the normalized graph into validated FileRef objects, filtering out paths that do not exist on disk."""
    source_files = normalize_source_files(G, repo_root)
    layout = detect_layout(repo_root)
    src_root = layout.source_root
    refs: dict[str, FileRef] = {}

    for sf, abs_path in source_files.items():
        try:
            rope_rel = str(abs_path.relative_to(repo_root))
        except ValueError:
            continue

        try:
            rel_to_src = abs_path.relative_to(src_root)
            module_path = str(rel_to_src).replace("/", ".")
        except ValueError:
            module_path = rope_rel.replace("/", ".")
        if module_path.endswith(".py"):
            module_path = module_path[:-3]

        refs[sf] = FileRef(
            graphify_source_file=sf,
            abs_path=abs_path,
            rope_rel=rope_rel,
            python_module=module_path,
        )

    return refs
