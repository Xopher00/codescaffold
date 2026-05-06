"""Typed data models for the contracts layer."""

from __future__ import annotations

from dataclasses import dataclass

from codescaffold.candidates import MoveCandidate


@dataclass(frozen=True)
class CycleReport:
    """One detected package-level cycle and an optional break suggestion."""

    cycle: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    suggested_break: MoveCandidate | None


@dataclass(frozen=True)
class ContractArtifact:
    """Result of a contract generation attempt."""

    config_path: str
    layers: tuple[tuple[str, ...], ...]
    forbidden: tuple[tuple[str, str], ...]
    cycles_detected: tuple[CycleReport, ...]
    written: bool


@dataclass(frozen=True)
class ContractValidationResult:
    """Typed result from running lint-imports."""

    succeeded: bool
    raw_output: str
    contracts_checked: int
    contracts_failed: int


@dataclass(frozen=True)
class ViolationReport:
    """Contract violation summary attached to an apply audit."""

    pre_apply_passed: bool
    post_apply_passed: bool
    is_regression: bool
    raw_output: str
