from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from refactor_plan.interface.cluster_view import ClusterView


class FileMoveProposal(BaseModel):
    source: str
    dest: str
    dest_package: str


class SymbolMoveProposal(BaseModel):
    source: str
    dest: str
    symbol: str
    approved: bool = False


class ClusterInfo(BaseModel):
    community_id: int
    source_files: list[str]
    proposed_package: str | None = None


class RefactorPlan(BaseModel):
    file_moves: list[FileMoveProposal] = []
    symbol_moves: list[SymbolMoveProposal] = []
    clusters: list[ClusterInfo] = []
    validation_commands: list[str] = [
        "python -m compileall .",
        "pytest -q",
    ]


_TEST_PARTS = {"tests", "test", "fixtures", "fixture", "conftest"}


def _detect_source_root(repo_root: Path, source_files: list[str]) -> Path:
    """Return the most likely source root for this repo.

    Checks common layouts: src/, lib/, the repo root itself.
    Skips test/fixture paths before sampling to avoid misdetection.
    Falls back to repo_root if no layout is detected.
    """
    candidates = [repo_root / "src", repo_root / "lib", repo_root]
    filtered = [
        sf for sf in source_files
        if not _TEST_PARTS.intersection(Path(sf).parts)
    ]
    sample = (filtered or source_files)[:20]
    for path in sample:
        for candidate in candidates:
            try:
                Path(path).relative_to(candidate)
                return candidate
            except ValueError:
                continue
    return repo_root


def plan(view: ClusterView, repo_root: Path, graph_json: Path) -> RefactorPlan:
    clusters: list[ClusterInfo] = []
    file_moves: list[FileMoveProposal] = []

    all_files = [sf for files in view.file_communities.values() for sf in files]
    src_root = _detect_source_root(repo_root, all_files)

    for comm_id, source_files in sorted(view.file_communities.items()):
        parents = [Path(sf).parent for sf in source_files]
        parent_counts = Counter(parents)
        _, majority_count = parent_counts.most_common(1)[0]

        if majority_count == len(source_files) and len(source_files) > 1:
            clusters.append(ClusterInfo(
                community_id=comm_id,
                source_files=source_files,
                proposed_package=None,
            ))
            continue

        target_dir = src_root / f"pkg_{comm_id:03d}"
        clusters.append(ClusterInfo(
            community_id=comm_id,
            source_files=source_files,
            proposed_package=str(target_dir),
        ))

        for sf in source_files:
            sf_path = Path(sf)
            if not sf_path.is_absolute():
                sf_path = (repo_root / sf_path).resolve()
            if sf_path.name == "__init__.py":
                continue
            if _TEST_PARTS.intersection(sf_path.parts):
                continue
            if sf_path.parent != target_dir:
                dest = str(target_dir / sf_path.name)
                file_moves.append(FileMoveProposal(
                    source=str(sf_path),
                    dest=dest,
                    dest_package=str(target_dir),
                ))

    return RefactorPlan(file_moves=file_moves, clusters=clusters)


def write_plan(refactor_plan: RefactorPlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(refactor_plan.model_dump_json(indent=2), encoding="utf-8")
    return path
