"""Plan data model. Persisted as JSON at .refactor_plan/refactor_plan.json."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class CandidateRecord(BaseModel):
    """Serialisable mirror of candidates.MoveCandidate — stored in the plan file."""

    kind: Literal["symbol", "module"]
    source_file: str
    symbol: str | None
    target_file: str
    community_id: int
    reasons: list[str]
    confidence: Literal["high", "medium", "low"]

    model_config = {"frozen": True}


class ApprovedMove(BaseModel):
    """A move the agent has approved for execution by the operations layer."""

    kind: Literal["symbol", "module"]
    source_file: str
    symbol: str | None = None
    target_file: str

    model_config = {"frozen": True}


class Plan(BaseModel):
    """Persisted refactor plan. graph_hash guards against stale execution."""

    graph_hash: str
    candidates: list[CandidateRecord] = Field(default_factory=list)
    approved_moves: list[ApprovedMove] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    model_config = {"frozen": True}
