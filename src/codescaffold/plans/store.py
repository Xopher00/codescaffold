"""Plan persistence: save, load, and freshness assertion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from codescaffold.candidates import MoveCandidate
from codescaffold.graphify import GraphSnapshot

from .schema import ApprovedMove, CandidateRecord, Plan, RopeResolutionRecord

if TYPE_CHECKING:
    from codescaffold.bridge.resolution import RopeResolution


def _preflight_from_status(status: str) -> str:
    if status == "resolved":
        return "ready"
    if status in ("ambiguous", "not_found"):
        return "needs_review"
    return "blocked"

DEFAULT_PLAN_PATH = Path(".refactor_plan/refactor_plan.json")


class StalePlanError(Exception):
    """The repo has changed since the plan was created; re-run analyze first."""

    def __init__(self, stored_hash: str, current_hash: str):
        super().__init__(
            f"Plan is stale: stored graph_hash {stored_hash[:12]}… "
            f"!= current {current_hash[:12]}…. Re-run analyze."
        )
        self.stored_hash = stored_hash
        self.current_hash = current_hash


def save(plan: Plan, path: Path = DEFAULT_PLAN_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2))


def load(path: Path = DEFAULT_PLAN_PATH) -> Plan:
    path = Path(path)
    return Plan.model_validate_json(path.read_text())


def assert_fresh(plan: Plan, snapshot: GraphSnapshot) -> None:
    """Raise StalePlanError if the plan's graph_hash no longer matches the repo."""
    if plan.graph_hash != snapshot.graph_hash:
        raise StalePlanError(plan.graph_hash, snapshot.graph_hash)


def candidates_to_records(
    candidates: list[MoveCandidate],
    resolutions: list[RopeResolution] | None = None,
) -> list[CandidateRecord]:
    records = []
    for i, c in enumerate(candidates):
        res = resolutions[i] if resolutions is not None else None
        resolution_record = (
            RopeResolutionRecord(
                status=res.status,
                symbol_kind=res.symbol_kind,
                line=res.line,
                near_misses=list(res.near_misses),
                reason=res.reason,
            )
            if res is not None
            else None
        )
        records.append(CandidateRecord(
            kind=c.kind,
            source_file=c.source_file,
            symbol=c.symbol,
            target_file=c.target_file,
            community_id=c.community_id,
            reasons=list(c.reasons),
            confidence=c.confidence,
            resolution=resolution_record,
            preflight=_preflight_from_status(res.status) if res is not None else None,
        ))
    return records


def records_to_candidates(records: list[CandidateRecord]) -> list[MoveCandidate]:
    return [
        MoveCandidate(
            kind=r.kind,
            source_file=r.source_file,
            symbol=r.symbol,
            target_file=r.target_file,
            community_id=r.community_id,
            reasons=tuple(r.reasons),
            confidence=r.confidence,
        )
        for r in records
    ]
