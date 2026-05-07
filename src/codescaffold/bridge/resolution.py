from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ResolutionStatus = Literal["resolved", "ambiguous", "not_found", "not_top_level", "not_python", "error"]
PreflightStatus = Literal["ready", "needs_review", "blocked"]


@dataclass(frozen=True)
class RopeResolution:
    status: ResolutionStatus
    symbol_kind: Literal["class", "function", "variable"] | None = None
    line: int | None = None
    near_misses: tuple[str, ...] = ()
    reason: str | None = None
