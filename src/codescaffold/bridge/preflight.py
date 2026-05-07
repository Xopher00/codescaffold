from __future__ import annotations

from collections.abc import Sequence
from difflib import get_close_matches
from pathlib import Path
from typing import Literal, Protocol

from codescaffold.operations.errors import RopeOperationError
from codescaffold.operations.results import SymbolInfo
from codescaffold.operations.rope_ops import close_rope_project, list_symbols

from .resolution import PreflightStatus, RopeResolution


class _Candidate(Protocol):
    kind: Literal["symbol", "module"]
    source_file: str
    symbol: str | None


def preflight_status(resolution: RopeResolution) -> PreflightStatus:
    if resolution.status == "resolved":
        return "ready"
    if resolution.status in ("ambiguous", "not_found"):
        return "needs_review"
    return "blocked"


def resolve_candidate(
    candidate: _Candidate,
    repo_path: Path,
    _cache: dict[str, list[SymbolInfo]] | None = None,
) -> RopeResolution:
    if candidate.kind == "module":
        return RopeResolution(status="resolved")

    if not candidate.source_file.endswith(".py"):
        return RopeResolution(
            status="not_python",
            reason=f"{candidate.source_file} is not a Python file",
        )

    try:
        if _cache is not None and candidate.source_file in _cache:
            symbols = _cache[candidate.source_file]
        else:
            symbols = list_symbols(str(repo_path), candidate.source_file)
            if _cache is not None:
                _cache[candidate.source_file] = symbols
    except RopeOperationError as e:
        return RopeResolution(status="error", reason=str(e))

    matches = [s for s in symbols if s.name == candidate.symbol]
    if len(matches) == 1:
        m = matches[0]
        return RopeResolution(status="resolved", symbol_kind=m.type, line=m.line)
    if len(matches) > 1:
        return RopeResolution(
            status="ambiguous",
            reason=f"{len(matches)} top-level symbols named '{candidate.symbol}'",
        )

    near = tuple(
        get_close_matches(candidate.symbol or "", [s.name for s in symbols], n=3, cutoff=0.6)
    )
    return RopeResolution(
        status="not_found",
        near_misses=near,
        reason=f"'{candidate.symbol}' not found in {candidate.source_file}",
    )


def resolve_candidates(
    candidates: Sequence[_Candidate],
    repo_path: Path,
) -> list[RopeResolution]:
    cache: dict[str, list[SymbolInfo]] = {}
    try:
        return [resolve_candidate(c, repo_path, _cache=cache) for c in candidates]
    finally:
        try:
            close_rope_project(str(repo_path))
        except RopeOperationError:
            pass
