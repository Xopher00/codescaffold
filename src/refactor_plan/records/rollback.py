from __future__ import annotations

from pathlib import Path

from rope.base.project import Project
from refactor_plan.execution.result import ApplyResult, MoveStrategy
from refactor_plan.records.manifests import read_manifest


def rollback(repo_root: Path, out_dir: Path) -> list[str]:
    """Undo the last apply batch. Returns list of actions taken."""
    result: ApplyResult | None = read_manifest(out_dir)
    if result is None:
        return ["No manifest found — nothing to rollback"]

    actions: list[str] = []

    rope_actions = [a for a in result.applied if a.strategy == MoveStrategy.ROPE]
    if rope_actions:
        project = Project(str(repo_root))
        try:
            for i, action in enumerate(reversed(rope_actions)):
                try:
                    project.history.undo()
                    actions.append(
                        f"rope undo: {action.source} <- {action.dest}"
                    )
                except Exception as exc:  # noqa: BLE001
                    remaining = len(rope_actions) - i - 1
                    actions.append(
                        f"rope undo failed for {action.source}: {exc}"
                        + (f" — {remaining} subsequent action(s) not attempted" if remaining else "")
                    )
        finally:
            project.close()

    libcst_actions = [
        a
        for a in result.applied
        if a.strategy == MoveStrategy.LIBCST and a.original_content is not None
    ]
    for action in libcst_actions:
        for filepath, content in (action.original_content or {}).items():
            try:
                Path(filepath).write_text(content, encoding="utf-8")
                actions.append(f"libcst restore: {filepath}")
            except Exception as exc:  # noqa: BLE001
                actions.append(f"libcst restore failed for {filepath}: {exc}")

    return actions
