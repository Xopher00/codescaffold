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


def plan(view: ClusterView, repo_root: Path, graph_json: Path) -> RefactorPlan:
    clusters: list[ClusterInfo] = []
    file_moves: list[FileMoveProposal] = []

    for comm_id, source_files in sorted(view.file_communities.items()):
        parents = [Path(sf).parent for sf in source_files]
        parent_counts = Counter(parents)
        _, majority_count = parent_counts.most_common(1)[0]

        if majority_count == len(source_files):
            clusters.append(ClusterInfo(
                community_id=comm_id,
                source_files=source_files,
                proposed_package=None,
            ))
            continue

        target_dir = repo_root / "src" / f"pkg_{comm_id:03d}"
        clusters.append(ClusterInfo(
            community_id=comm_id,
            source_files=source_files,
            proposed_package=str(target_dir),
        ))

        for sf in source_files:
            sf_path = Path(sf)
            if sf_path.parent != target_dir:
                dest = str(target_dir / sf_path.name)
                file_moves.append(FileMoveProposal(
                    source=sf,
                    dest=dest,
                    dest_package=str(target_dir),
                ))

    return RefactorPlan(file_moves=file_moves, clusters=clusters)


def write_plan(refactor_plan: RefactorPlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(refactor_plan.model_dump_json(indent=2), encoding="utf-8")
    return path
