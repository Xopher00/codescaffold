

from pydantic import BaseModel
from refactor_plan.execution.models import ClusterInfo, FileMoveProposal

class SymbolMoveProposal(BaseModel):
    """Proposal to extract a single named symbol from one file into another, generated from graph clustering evidence."""
    source: str
    dest: str
    symbol: str
    approved: bool = False



class PendingDecision(BaseModel):
    """A community whose files span multiple directories and requires human placement input before an automated move can be approved."""
    community_id: int
    source_files: list[str]
    current_dirs: dict[str, list[str]]  # dir_path → [file_paths]
    needs_placement: bool               # True when files span multiple directories
    cohesion: float | None
    risk_level: str
    cross_cluster_edges: list[dict]     # top edges leaving this community
    surprising_connections: list[dict]  # surprising_connections entries for files here



class RefactorPlan(BaseModel):
    """Immutable plan artifact produced by the planner, bundling file moves, symbol moves, and pending placement decisions for a single refactor pass."""
    file_moves: list[FileMoveProposal] = []       # populated by approve_moves, not plan()
    symbol_moves: list[SymbolMoveProposal] = []
    clusters: list[ClusterInfo] = []
    pending_decisions: list[PendingDecision] = []
    source_root: str | None = None
    validation_commands: list[str] = [
        "python -m compileall .",
        "pytest -q",
    ]
