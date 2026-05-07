from __future__ import annotations

from dataclasses import dataclass

from .errors import RopeOperationError
from .results import RopeChangeResult
from .rope_ops import close_rope_project, rename_symbol


@dataclass(frozen=True)
class RenameEntry:
    file_path: str
    old_name: str
    new_name: str


@dataclass(frozen=True)
class BatchRenameResult:
    applied: tuple[RenameEntry, ...]
    rope_results: tuple[RopeChangeResult, ...]
    error: str | None = None  # set if stopped mid-batch; None on full success


def rename_symbol_batch(
    entries: list[RenameEntry],
    project_path: str,
) -> BatchRenameResult:
    """Apply all entries in a single rope project session; stop on first error.

    Mirrors bridge.preflight.resolve_candidates: one session for the whole
    batch, one close_rope_project in finally. Rope tracks intra-session
    changes; closing between renames would discard working state and break
    multi-rename-in-one-file flows.
    """
    applied: list[RenameEntry] = []
    rope_results: list[RopeChangeResult] = []
    error: str | None = None
    try:
        for entry in entries:
            try:
                result = rename_symbol(
                    project_path, entry.file_path, entry.old_name, entry.new_name
                )
            except RopeOperationError as e:
                error = f"{entry.old_name} → {entry.new_name}: {e}"
                break
            applied.append(entry)
            rope_results.append(result)
    finally:
        try:
            close_rope_project(project_path)
        except RopeOperationError:
            pass
    return BatchRenameResult(
        applied=tuple(applied),
        rope_results=tuple(rope_results),
        error=error,
    )
