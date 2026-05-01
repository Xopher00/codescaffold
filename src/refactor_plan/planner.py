"""Planner: turn a GraphView into a RefactorPlan and serialize it.

Algorithm overview
------------------
1. Allocate pkg_NNN names: sort file_clusters by len(files) descending,
   tie-break by lowest cluster id. Largest → pkg_001, next → pkg_002, etc.
2. File moves: dest = pkg_NNN/<basename>. Skip if src == dest (no-op).
3. Symbol moves: direct passthrough from view.misplaced_symbols.
4. Shim candidates: repo-introspection only (ast + tomllib), three triggers:
   A. file stem appears in any top-level __init__.py's __all__ (exact match).
   B. file is directly under <repo_root>/src/ or <repo_root>/ (top-level pkg).
   C. file appears in pyproject.toml [project.scripts] or [project.entry-points.*].
5. Splitting candidates: passthrough from view.suggested_questions, types
   "low_cohesion" and "bridge_node" only, sorted by (type, question).
6. All lists sorted by stable keys for determinism.

Shim matching rule (Trigger A)
-------------------------------
A file's stem (basename without .py) is compared against every entry in
__all__ using exact string comparison. No case-folding.

For the fixture: messy_pkg/__init__.py has __all__ = ["Vec", "Reader"].
The stems "vec" and "reader" do NOT match "Vec" and "Reader" exactly, so
no Trigger A shim candidates are emitted. Trigger B fires only if the file
lives directly under <repo_root>/src/ or <repo_root>/ itself. Trigger C
fires only if the file path appears in pyproject.toml scripts/entry-points.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-reuse-def]

from pydantic import BaseModel

from refactor_plan.cluster_view import GraphView


class FileMove(BaseModel):
    src: str        # repo-relative source path
    dest: str       # pkg_NNN/<basename>
    cluster: str    # e.g. "pkg_001"
    cohesion: float # cluster cohesion (passthrough)


class SymbolMove(BaseModel):
    symbol_id: str
    label: str
    src_file: str
    dest_cluster: str    # pkg_NNN where target_community lives
    host_community: int
    target_community: int
    approved: bool = False  # human-gated


class ShimCandidate(BaseModel):
    src: str                  # repo-relative original path
    triggers: list[str]       # which heuristic(s) fired


class SplittingCandidate(BaseModel):
    """Passthrough from suggest_questions[low_cohesion]/[bridge_node]."""
    type: str      # "low_cohesion" | "bridge_node"
    question: str
    why: str


class ClusterAlloc(BaseModel):
    name: str          # "pkg_001"
    community_id: int
    files: list[str]
    cohesion: float


class RefactorPlan(BaseModel):
    clusters: list[ClusterAlloc]
    file_moves: list[FileMove]
    symbol_moves: list[SymbolMove]
    shim_candidates: list[ShimCandidate]
    splitting_candidates: list[SplittingCandidate]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _allocate_pkg_names(view: GraphView) -> dict[int, str]:
    """Sort clusters by (size desc, community_id asc) → pkg_001, pkg_002, …"""
    sorted_clusters = sorted(
        view.file_clusters,
        key=lambda fc: (-len(fc.files), fc.id),
    )
    return {fc.id: f"pkg_{rank:03d}" for rank, fc in enumerate(sorted_clusters, start=1)}


def _parse_all_from_init(init_path: Path) -> set[str]:
    """Return the set of names in __all__ from an __init__.py, or empty set."""
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, (list, tuple)):
                            return {str(x) for x in value}
                    except (ValueError, TypeError):
                        pass
    return set()


def _collect_all_exports(repo_root: Path) -> set[str]:
    """Collect all names from every top-level __init__.py under repo_root."""
    exports: set[str] = set()
    for init_path in repo_root.rglob("__init__.py"):
        exports |= _parse_all_from_init(init_path)
    return exports


def _parse_pyproject_scripts(repo_root: Path) -> set[str]:
    """Return file paths (values) from pyproject.toml [project.scripts] and
    [project.entry-points.*]. Entry-point values are of the form
    'pkg.module:callable'; we extract the module path portion."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return set()
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return set()
    refs: set[str] = set()
    project = data.get("project", {})
    # [project.scripts] values like "module.sub:func"
    for val in project.get("scripts", {}).values():
        refs.add(val)
    # [project.entry-points.*]
    for group in project.get("entry-points", {}).values():
        for val in group.values():
            refs.add(val)
    return refs


def _shim_triggers(src: str, repo_root: Path, all_exports: set[str], pyproject_refs: set[str]) -> list[str]:
    """Return list of trigger names that fire for this source file."""
    triggers: list[str] = []
    p = Path(src)
    stem = p.stem  # filename without .py

    # Trigger A: stem appears exactly in any __all__
    if stem in all_exports:
        triggers.append("in __all__")

    # Trigger B: file is directly under <repo_root>/src/ or <repo_root>/
    try:
        full = (repo_root / src).resolve()
        top_src = (repo_root / "src").resolve()
        top_root = repo_root.resolve()
        if full.parent == top_src or full.parent == top_root:
            triggers.append("top_level")
    except Exception:
        pass

    # Trigger C: file appears in pyproject.toml scripts/entry-points values
    # Values are dotted module paths like "refactor_plan.cli:app"
    # Check if the dotted module path corresponds to this file.
    src_no_py = str(p.with_suffix("")).replace("/", ".").replace("\\", ".")
    for ref in pyproject_refs:
        # strip the ":callable" suffix if present
        module_part = ref.split(":")[0].strip()
        if module_part == src_no_py or src_no_py.endswith(module_part):
            triggers.append("pyproject_scripts")
            break

    return triggers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan(view: GraphView, repo_root: Path) -> RefactorPlan:
    """Turn a GraphView into a RefactorPlan (deterministic, no new analysis)."""

    # 1. Allocate pkg_NNN names.
    community_to_pkg = _allocate_pkg_names(view)

    # Build sorted ClusterAlloc list (same order as community_to_pkg allocation).
    sorted_clusters = sorted(
        view.file_clusters,
        key=lambda fc: (-len(fc.files), fc.id),
    )
    clusters = [
        ClusterAlloc(
            name=community_to_pkg[fc.id],
            community_id=fc.id,
            files=sorted(fc.files),
            cohesion=fc.cohesion,
        )
        for fc in sorted_clusters
    ]

    # 2. File moves (sorted by src for determinism).
    file_moves: list[FileMove] = []
    for fc in sorted_clusters:
        pkg_name = community_to_pkg[fc.id]
        for src in sorted(fc.files):
            dest = f"{pkg_name}/{Path(src).name}"
            if src == dest:
                continue
            file_moves.append(
                FileMove(
                    src=src,
                    dest=dest,
                    cluster=pkg_name,
                    cohesion=fc.cohesion,
                )
            )
    # Sort globally by (cluster, src) for determinism.
    file_moves.sort(key=lambda m: (m.cluster, m.src))

    # 3. Symbol moves (passthrough, all approved=False).
    symbol_moves: list[SymbolMove] = [
        SymbolMove(
            symbol_id=m.symbol_id,
            label=m.label,
            src_file=m.host_file,
            dest_cluster=community_to_pkg[m.target_community],
            host_community=m.host_community,
            target_community=m.target_community,
            approved=False,
        )
        for m in view.misplaced_symbols
    ]
    # misplaced_symbols already sorted by (host_file, label) in cluster_view.
    symbol_moves.sort(key=lambda s: (s.src_file, s.label))

    # 4. Shim candidates (repo-introspection only).
    all_exports = _collect_all_exports(repo_root)
    pyproject_refs = _parse_pyproject_scripts(repo_root)

    shim_map: dict[str, list[str]] = {}  # src → triggers
    for fc in view.file_clusters:
        for src in fc.files:
            triggers = _shim_triggers(src, repo_root, all_exports, pyproject_refs)
            if triggers:
                if src not in shim_map:
                    shim_map[src] = triggers
                else:
                    # merge triggers (dedup)
                    existing = set(shim_map[src])
                    for t in triggers:
                        if t not in existing:
                            shim_map[src].append(t)

    shim_candidates = [
        ShimCandidate(src=src, triggers=sorted(triggers))
        for src, triggers in sorted(shim_map.items())
    ]

    # 5. Splitting candidates (passthrough, sorted by (type, question)).
    splitting_candidates = sorted(
        [
            SplittingCandidate(
                type=q["type"],
                question=q["question"],
                why=q["why"],
            )
            for q in view.suggested_questions
            if q.get("type") in ("low_cohesion", "bridge_node")
        ],
        key=lambda c: (c.type, c.question),
    )

    return RefactorPlan(
        clusters=clusters,
        file_moves=file_moves,
        symbol_moves=symbol_moves,
        shim_candidates=shim_candidates,
        splitting_candidates=splitting_candidates,
    )


def write_plan(refactor_plan: RefactorPlan, output_path: Path) -> None:
    """Serialize RefactorPlan to JSON at output_path."""
    output_path.write_text(refactor_plan.model_dump_json(indent=2))
