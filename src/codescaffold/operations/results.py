from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RopeChangeResult:
    """Result of a successful Rope refactoring operation."""

    changed_files: tuple[str, ...]
    new_path: str | None = None
    manually_fixed_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SymbolInfo:
    """A top-level symbol in a Python file."""

    name: str
    type: Literal["class", "function", "variable"]
    line: int
    col_offset: int
    byte_offset: int
