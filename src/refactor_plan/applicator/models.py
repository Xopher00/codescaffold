from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel


@dataclass
class FileRef:
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
    FILE = "FILE"
    SYMBOL = "SYMBOL"


class MoveStrategy(str, Enum):
    ROPE = "rope"
    LIBCST = "libcst"


class AppliedAction(BaseModel):
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
    kind: MoveKind
    source: str
    dest: str | None = None
    symbol: str | None = None
    reason: str
    category: str
    strategy_attempted: MoveStrategy | None = None


class ApplyResult(BaseModel):
    applied: list[AppliedAction] = []
    skipped: list[Escalation] = []
    failed: list[Escalation] = []
    blocked: list[Escalation] = []
