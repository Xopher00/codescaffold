from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import hashlib
from typing import Any, Iterable


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    kind: str | None
    path: Path | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str | None
    confidence: str | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class GraphifyGraph:
    source_path: Path
    sha256: str
    nodes: dict[str, GraphNode]
    edges: tuple[GraphEdge, ...]


@dataclass(frozen=True)
class MoveCandidate:
    source_path: Path
    target_package: str
    reason: str
    evidence_node_ids: tuple[str, ...]
    graph_sha256: str
    confidence: str | None = None


_PATH_KEYS = (
    "path",
    "file",
    "filepath",
    "file_path",
    "source_path",
    "relative_path",
)

_LABEL_KEYS = (
    "label",
    "name",
    "title",
    "qualified_name",
    "symbol",
    "id",
)

_KIND_KEYS = (
    "kind",
    "type",
    "node_type",
    "category",
)

_RELATION_KEYS = (
    "relation",
    "relationship",
    "kind",
    "type",
    "edge_type",
)

_CONFIDENCE_KEYS = (
    "confidence",
    "confidence_tag",
    "provenance",
)


def load_graphify_graph(graph_json: str | Path, *, repo_root: str | Path) -> GraphifyGraph:
    graph_path = Path(graph_json).resolve()
    repo_root = Path(repo_root).resolve()

    raw_bytes = graph_path.read_bytes()
    raw = json.loads(raw_bytes)
    digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    raw_nodes = _get_nodes(raw)
    raw_edges = _get_edges(raw)

    nodes: dict[str, GraphNode] = {}
    for item in raw_nodes:
        node_id = str(item.get("id") or item.get("key") or _first_present(item, _LABEL_KEYS))
        if not node_id or node_id == "None":
            raise ValueError(f"Graph node is missing stable id/label: {item!r}")

        label = str(_first_present(item, _LABEL_KEYS) or node_id)
        kind = _optional_str(_first_present(item, _KIND_KEYS))
        path = _extract_repo_path(item, repo_root)

        nodes[node_id] = GraphNode(
            id=node_id,
            label=label,
            kind=kind,
            path=path,
            raw=item,
        )

    edges = tuple(_parse_edge(edge) for edge in raw_edges)

    _validate_edge_endpoints(edges, nodes)

    return GraphifyGraph(
        source_path=graph_path,
        sha256=digest,
        nodes=nodes,
        edges=edges,
    )


def candidate_moves_by_cluster(
    graph: GraphifyGraph,
    *,
    cluster_key: str = "community",
    target_package_by_cluster: dict[str, str],
    min_confidence: frozenset[str] = frozenset({"EXTRACTED", "INFERRED"}),
) -> list[MoveCandidate]:
    """Convert graph cluster membership into explicit move candidates.

    Does not apply anything — only emits typed candidates.
    """
    candidates: list[MoveCandidate] = []

    for node in graph.nodes.values():
        if node.path is None:
            continue
        if node.path.suffix != ".py":
            continue

        cluster = node.raw.get(cluster_key) or node.raw.get("community_id") or node.raw.get("cluster")
        if cluster is None:
            continue

        cluster = str(cluster)
        target_package = target_package_by_cluster.get(cluster)
        if not target_package:
            continue

        confidence = _optional_str(
            node.raw.get("confidence")
            or node.raw.get("confidence_tag")
            or node.raw.get("provenance")
        )

        if confidence and confidence not in min_confidence:
            continue

        candidates.append(
            MoveCandidate(
                source_path=node.path,
                target_package=target_package,
                reason=f"Graphify node {node.id!r} belongs to cluster {cluster!r}",
                evidence_node_ids=(node.id,),
                graph_sha256=graph.sha256,
                confidence=confidence,
            )
        )

    return candidates


def _get_nodes(raw: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("nodes", "vertices"):
        value = raw.get(key)
        if isinstance(value, list):
            return [_require_dict(x, f"{key}[]") for x in value]

    if isinstance(raw.get("graph"), dict):
        return _get_nodes(raw["graph"])

    raise ValueError("Could not find graph nodes. Expected top-level 'nodes' list.")


def _get_edges(raw: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("links", "edges"):
        value = raw.get(key)
        if isinstance(value, list):
            return [_require_dict(x, f"{key}[]") for x in value]

    if isinstance(raw.get("graph"), dict):
        return _get_edges(raw["graph"])

    return []


def _parse_edge(item: dict[str, Any]) -> GraphEdge:
    source = item.get("source") or item.get("from") or item.get("src")
    target = item.get("target") or item.get("to") or item.get("dst")

    if isinstance(source, dict):
        source = source.get("id") or source.get("name")
    if isinstance(target, dict):
        target = target.get("id") or target.get("name")

    if source is None or target is None:
        raise ValueError(f"Graph edge missing source/target: {item!r}")

    return GraphEdge(
        source=str(source),
        target=str(target),
        relation=_optional_str(_first_present(item, _RELATION_KEYS)),
        confidence=_optional_str(_first_present(item, _CONFIDENCE_KEYS)),
        raw=item,
    )


def _extract_repo_path(item: dict[str, Any], repo_root: Path) -> Path | None:
    raw_path = _first_present(item, _PATH_KEYS)
    if not raw_path:
        return None

    path = Path(str(raw_path))

    if path.is_absolute():
        try:
            path = path.resolve().relative_to(repo_root)
        except ValueError:
            return None

    return Path(path.as_posix())


def _first_present(item: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Expected dict for {label}, got {type(value).__name__}")
    return value


def _validate_edge_endpoints(edges: Iterable[GraphEdge], nodes: dict[str, GraphNode]) -> None:
    missing: list[tuple[str, str]] = []
    for edge in edges:
        if edge.source not in nodes:
            missing.append(("source", edge.source))
        if edge.target not in nodes:
            missing.append(("target", edge.target))
    # Lenient: graphify may reference external/string nodes not in the node list.
    # Raise here to enable strict mode.
    if missing:
        return
