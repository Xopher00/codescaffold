"""Typed wrappers over graphify's analysis functions.

Returns frozen dataclasses instead of raw dicts. Does not use any
private (_-prefixed) graphify symbols.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vendor import _god_nodes, _surprising_connections
from .snapshot import GraphSnapshot


@dataclass(frozen=True)
class GodNode:
    """A highly-connected node — a core abstraction in the codebase."""

    id: str
    label: str
    degree: int


@dataclass(frozen=True)
class SurprisingEdge:
    """A cross-community or cross-file edge that signals structural tension."""

    source: str
    target: str
    source_files: tuple[str, ...]
    confidence: str
    relation: str
    reason: str


def god_nodes(snap: GraphSnapshot, top_n: int = 10) -> list[GodNode]:
    """Return the top_n most-connected real entities in the graph."""
    raw = _god_nodes(snap.graph, top_n=top_n)
    return [GodNode(id=n["id"], label=n["label"], degree=n["degree"]) for n in raw]


def cohesion(snap: GraphSnapshot) -> dict[int, float]:
    """Return cohesion score per community (0.0–1.0; >0.4 = well-coupled)."""
    return snap.cohesion_scores()


def surprises(snap: GraphSnapshot, top_n: int = 5) -> list[SurprisingEdge]:
    """Return the most structurally surprising cross-community connections."""
    raw = _surprising_connections(snap.graph, snap.communities, top_n=top_n)
    return [
        SurprisingEdge(
            source=e["source"],
            target=e["target"],
            source_files=tuple(e.get("source_files", [])),
            confidence=e.get("confidence", "EXTRACTED"),
            relation=e.get("relation", ""),
            reason=e.get("why", e.get("note", "")),
        )
        for e in raw
    ]
