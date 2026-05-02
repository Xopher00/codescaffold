from __future__ import annotations

import json
import logging
from pathlib import Path

from anthropic import Anthropic
from anthropic.types import TextBlock
from pydantic import BaseModel

from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.planning.planner import RefactorPlan

logger = logging.getLogger(__name__)


class RenameEntry(BaseModel):
    old_name: str
    new_name: str
    rationale: str = ""


class RenameMap(BaseModel):
    entries: list[RenameEntry] = []


def name_clusters(
    refactor_plan: RefactorPlan,
    view: ClusterView,
    repo_root: Path,
    graph_json: Path,
    model: str = "claude-opus-4-7",
) -> RenameMap:
    clusters_with_placeholder = [
        c for c in refactor_plan.clusters if c.proposed_package
    ]
    if not clusters_with_placeholder:
        return RenameMap()

    context_lines = [
        f"- pkg_{c.community_id:03d}: "
        + ", ".join(Path(sf).name for sf in c.source_files)
        for c in clusters_with_placeholder
    ]

    prompt = (
        "You are helping rename placeholder package names in a refactored Python codebase.\n\n"
        "Each cluster is named pkg_NNN. Based on its files, suggest a short snake_case name.\n\n"
        "Clusters:\n" + "\n".join(context_lines) + "\n\n"
        "Return ONLY a JSON object mapping placeholder names to proposed names:\n"
        '{"pkg_001": "auth", "pkg_002": "data_pipeline"}'
    )

    try:
        client = Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        block = response.content[0]
        raw = block.text.strip() if isinstance(block, TextBlock) else ""
        rename_dict: dict[str, str] = json.loads(raw)
    except Exception as exc:
        logger.warning("LLM naming failed: %s", exc)
        return RenameMap()

    return RenameMap(entries=[
        RenameEntry(old_name=old, new_name=new)
        for old, new in rename_dict.items()
    ])


def write_rename_map(rename_map: RenameMap, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rename_map.model_dump_json(indent=2), encoding="utf-8")
    return path
