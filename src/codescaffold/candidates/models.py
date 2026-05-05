from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MoveCandidate:
    """A graph-evidence-backed suggestion to move a symbol or module.

    Emitted by propose_moves(). Approved candidates are written into a Plan
    and executed by the operations layer inside a sandbox.
    """

    kind: Literal["symbol", "module"]
    source_file: str          # repo-relative path, e.g. "src/pkg/utils.py"
    symbol: str | None        # class/function name; None for module moves
    target_file: str          # proposed destination, e.g. "src/pkg/core.py"
    community_id: int         # graphify community the symbol currently lives in
    reasons: tuple[str, ...]
    confidence: Literal["high", "medium", "low"]
