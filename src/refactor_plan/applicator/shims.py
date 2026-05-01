"""Applicator: emit compatibility shim files after rope has moved modules.

For each ShimCandidate in the plan, write a 3-line compat-shim at the
original src path so external importers continue to work after the move.

Trigger D (detect_external_access) augments planner triggers A/B/C: it
checks whether any Python file outside the moved file's package imports
from that module. This is implemented via substring search (MVP-safe),
not LibCST ScopeProvider, which is noted as optional in the spec.

Public API
----------
emit_shims(plan, repo_root, *, mode="auto") -> list[Path]
detect_external_access(src_file, repo_root) -> bool
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from refactor_plan.planner import FileMove, RefactorPlan, ShimCandidate

_SHIM_TEMPLATE = (
    "# auto-generated compat shim — original path preserved for external importers\n"
    "from {dest_pkg}.{basename} import *  # noqa: F401,F403\n"
    "__deprecated_path__ = True\n"
)


def _shim_content(dest_pkg: str, basename: str) -> str:
    return _SHIM_TEMPLATE.format(dest_pkg=dest_pkg, basename=basename)


def _write_shim(repo_root: Path, fm: FileMove) -> Path:
    """Write a compat shim at repo_root / fm.src and return its path."""
    dest_pkg = fm.cluster
    basename = Path(fm.src).stem
    content = _shim_content(dest_pkg, basename)
    shim_path = repo_root / fm.src
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    shim_path.write_text(content, encoding="utf-8")
    return shim_path


def detect_external_access(src_file: Path, repo_root: Path) -> bool:
    """True if any Python file outside src_file's package imports from it.

    Trigger D: substring-based implementation (MVP). Checks whether any
    .py file outside src_file's parent directory contains an import of the
    form 'from <pkg>.<mod>' or 'import <pkg>.<mod>'.

    LibCST ScopeProvider would be the next step for correctness on dynamic
    imports; the substring check is sufficient for the fixture and most
    real-world cases involving explicit imports.
    """
    src_pkg = src_file.parent
    src_mod_name = src_file.stem
    pkg_name = src_pkg.name

    for other in repo_root.rglob("*.py"):
        if other == src_file or other.is_relative_to(src_pkg):
            continue
        try:
            text = other.read_text(encoding="utf-8")
        except OSError:
            continue
        if (
            f"from {pkg_name}.{src_mod_name}" in text
            or f"import {pkg_name}.{src_mod_name}" in text
        ):
            return True
    return False


def emit_shims(
    plan: RefactorPlan,
    repo_root: Path,
    *,
    mode: Literal["auto", "always", "never"] = "auto",
) -> list[Path]:
    """Return list of shim file paths created.

    mode="never"  — no shims written.
    mode="always" — one shim per FileMove, regardless of heuristics.
    mode="auto"   — shim for each ShimCandidate; also runs trigger D on
                    remaining FileMoves and shims any with external importers.
    """
    if mode == "never":
        return []

    # Build a lookup from src path → FileMove for quick access.
    src_to_fm: dict[str, FileMove] = {fm.src: fm for fm in plan.file_moves}

    if mode == "always":
        created: list[Path] = []
        for fm in plan.file_moves:
            created.append(_write_shim(repo_root, fm))
        return created

    # mode == "auto"
    created = []
    shimmed_srcs: set[str] = set()

    # Shim every ShimCandidate (planner already ran triggers A/B/C).
    for sc in plan.shim_candidates:
        fm = src_to_fm.get(sc.src)
        if fm is None:
            # No corresponding file move; skip.
            continue
        created.append(_write_shim(repo_root, fm))
        shimmed_srcs.add(sc.src)

    # Trigger D: check remaining FileMoves for external importers.
    for fm in plan.file_moves:
        if fm.src in shimmed_srcs:
            continue
        src_file = repo_root / fm.src
        if detect_external_access(src_file, repo_root):
            created.append(_write_shim(repo_root, fm))
            shimmed_srcs.add(fm.src)

    return created
