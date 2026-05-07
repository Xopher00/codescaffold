"""Plan data model. Persisted as JSON at .refactor_plan/refactor_plan.json."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class RopeResolutionRecord(BaseModel):
    """Serialisable mirror of bridge.RopeResolution — stored in the plan file.

    None defaults on all optional fields preserve load-compat with plans written
    before preflight was introduced.
    """

    status: Literal["resolved", "ambiguous", "not_found", "not_top_level", "not_python", "error"]
    symbol_kind: Literal["class", "function", "variable"] | None = None
    line: int | None = None
    near_misses: list[str] = Field(default_factory=list)
    reason: str | None = None

    model_config = {"frozen": True}


class CandidateRecord(BaseModel):
    """Serialisable mirror of candidates.MoveCandidate — stored in the plan file."""

    kind: Literal["symbol", "module"]
    source_file: str
    symbol: str | None
    target_file: str
    community_id: int
    reasons: list[str]
    confidence: Literal["high", "medium", "low"]
    # Preflight fields — None for plans written before preflight was introduced.
    resolution: RopeResolutionRecord | None = None
    preflight: Literal["ready", "needs_review", "blocked"] | None = None

    model_config = {"frozen": True}


class ApprovedMove(BaseModel):
    """A move the agent has approved for execution by the operations layer."""

    kind: Literal["symbol", "module"]
    source_file: str
    symbol: str | None = None
    target_file: str

    model_config = {"frozen": True}


class ApprovedRename(BaseModel):
    """A symbol rename the agent has approved for execution.

    Preflight fields mirror CandidateRecord so the audit trail records why
    each rename was admitted. None defaults preserve load-compat with plans
    written before rename support was added.
    """

    file_path: str
    old_name: str
    new_name: str
    resolution: RopeResolutionRecord | None = None
    preflight: Literal["ready", "needs_review", "blocked"] | None = None

    model_config = {"frozen": True}


class Plan(BaseModel):
    """Persisted refactor plan. graph_hash guards against stale execution."""

    graph_hash: str
    candidates: list[CandidateRecord] = Field(default_factory=list)
    approved_moves: list[ApprovedMove] = Field(default_factory=list)
    approved_renames: list[ApprovedRename] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    model_config = {"frozen": True}
