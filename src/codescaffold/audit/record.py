"""ApplyAudit: an immutable record of one apply operation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from codescaffold.operations import RopeChangeResult
from codescaffold.plans import ApprovedMove
from codescaffold.validation import ValidationResult


@dataclass(frozen=True)
class ApplyAudit:
    plan_hash: str
    sandbox_branch: str
    moves_applied: tuple[ApprovedMove, ...]
    rope_results: tuple[RopeChangeResult, ...]
    validation: ValidationResult
    succeeded: bool
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(tz=timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps({
            "plan_hash": self.plan_hash,
            "sandbox_branch": self.sandbox_branch,
            "moves_applied": [m.model_dump() for m in self.moves_applied],
            "rope_results": [
                {
                    "changed_files": list(r.changed_files),
                    "new_path": r.new_path,
                    "manually_fixed_files": list(r.manually_fixed_files),
                    "warnings": list(r.warnings),
                }
                for r in self.rope_results
            ],
            "validation": {
                "compileall_ok": self.validation.compileall_ok,
                "pytest_ok": self.validation.pytest_ok,
                "pytest_summary": self.validation.pytest_summary,
                "failed_steps": list(self.validation.failed_steps),
                "contracts_ok": self.validation.contracts_ok,
            },
            "succeeded": self.succeeded,
            "timestamp": self.timestamp,
        }, indent=2)

    def save(self, audit_dir: Path) -> Path:
        audit_dir = Path(audit_dir)
        audit_dir.mkdir(parents=True, exist_ok=True)
        ts = self.timestamp.replace(":", "-").replace("+", "").replace(".", "-")
        path = audit_dir / f"{ts}.json"
        path.write_text(self.to_json())
        return path
