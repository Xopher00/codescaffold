from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel


@dataclass
class FileRef:
    """Validated reference to a source file that rope can resolve, used to gate moves before any files are touched."""
    graphify_source_file: str
    abs_path: Path
    rope_rel: str
    python_module: str

    def validate_rope_resolvable(self, project_root: Path) -> bool:
        try:
            self.abs_path.relative_to(project_root)
        except ValueError:
            return False
        return self.abs_path.exists()


class MoveKind(str, Enum):
    """Discriminates between a whole-file move and a single-symbol extraction move."""
    FILE = "FILE"
    SYMBOL = "SYMBOL"
    PACKAGE = "PACKAGE"


class MoveStrategy(str, Enum):
    """Indicates which mechanical tool (rope or LibCST) was used to execute a move."""
    ROPE = "rope"
    LIBCST = "libcst"


class AppliedAction(BaseModel):
    """Record of a single successfully executed move — file or symbol — including the strategy used, files touched, and original content for rollback."""
    kind: MoveKind
    source: str
    dest: str
    symbol: str | None = None
    strategy: MoveStrategy | None = None
    files_touched: list[str] = []
    imports_rewritten: int = 0
    original_content: dict[str, str] | None = None
    validation_passed: bool | None = None


class Escalation(BaseModel):
    """Signals that a move could not be completed mechanically and requires human review, capturing the reason and strategy attempted."""
    kind: MoveKind
    source: str
    dest: str | None = None
    symbol: str | None = None
    reason: str
    category: str
    strategy_attempted: MoveStrategy | None = None


class ApplyResult(BaseModel):
    """Aggregated outcome of an apply pass: lists of successfully applied actions, escalated failures, skipped moves, and blocked items."""
    applied: list[AppliedAction] = []
    skipped: list[Escalation] = []
    failed: list[Escalation] = []
    blocked: list[Escalation] = []



class FileMoveProposal(BaseModel):
    """Proposal to move a source file into a destination package directory, with an optional rationale and risk level."""
    source: str
    dest: str
    dest_package: str



class ClusterInfo(BaseModel):
    """Structural metadata for a detected community: its files, cohesion score, cross-cluster edges, and proposed destination package."""
    community_id: int
    source_files: list[str]
    proposed_package: str | None = None
    cohesion: float | None = None
    risk_level: str | None = None  # LOW / MEDIUM / HIGH based on cohesion score
