from __future__ import annotations

import json
from pathlib import Path
from refactor_plan.execution.models import ApplyResult

_INIT_MANIFEST = "refactor_manifest.json"
_STRAY_MANIFEST = "stray_deleted_manifest.json"


def write_manifest(result: ApplyResult, out_dir: Path) -> Path:
    """Serialise an ApplyResult to a timestamped JSON manifest file in out_dir, recording every applied action and escalation for audit and rollback."""
    path = out_dir / _INIT_MANIFEST
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_manifest(out_dir: Path) -> ApplyResult | None:
    path = out_dir / _INIT_MANIFEST
    if not path.exists():
        return None
    return ApplyResult.model_validate_json(path.read_text(encoding="utf-8"))


def write_stray_manifest(deleted: list[str], out_dir: Path) -> Path:
    path = out_dir / _STRAY_MANIFEST
    path.write_text(json.dumps({"deleted": deleted}, indent=2), encoding="utf-8")
    return path


def read_stray_manifest(out_dir: Path) -> list[str]:
    path = out_dir / _STRAY_MANIFEST
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("deleted", [])
