from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import refactor_plan.cli as cli
from refactor_plan.applicator.rope_runner import AppliedAction, ApplyResult
from refactor_plan.namer import RenameEntry, RenameMap

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    fixture_src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(fixture_src, dst)
    result = runner.invoke(cli.app, ["analyze", str(dst)])
    assert result.exit_code == 0
    return dst


def _rename_map() -> RenameMap:
    return RenameMap(
        entries=[
            RenameEntry(
                old_name="pkg_001",
                new_name="core",
                rationale="test",
            )
        ]
    )


def test_name_writes_rename_map(repo: Path, monkeypatch) -> None:
    out = repo / ".refactor_plan" / "rename_map.json"
    out.unlink(missing_ok=True)
    monkeypatch.setattr(
        cli,
        "propose_rename_map",
        lambda refactor_plan, view, repo_root, graph_path: _rename_map(),
    )

    result = runner.invoke(cli.app, ["name", str(repo)])

    assert result.exit_code == 0
    assert "wrote rename_map.json" in result.stdout
    assert out.exists()
    recovered = RenameMap.model_validate_json(out.read_text())
    assert recovered.entries[0].new_name == "core"


def test_name_apply_uses_existing_map_and_validates(repo: Path, monkeypatch) -> None:
    out = repo / ".refactor_plan" / "rename_map.json"
    out.write_text(_rename_map().model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_rename_map(rename_map, repo_root):
        seen["entries"] = len(rename_map.entries)
        return ApplyResult(
            applied=[
                AppliedAction(kind="rename", description="rename", history_index=2)
            ],
            escalations=[],
        )

    def fake_validate(repo_root, applied_count, **kwargs):
        seen["applied_count"] = applied_count
        return SimpleNamespace(passed=True, rolled_back=False)

    monkeypatch.setattr(cli, "_apply_rename_map", fake_apply_rename_map)
    monkeypatch.setattr(cli, "validate", fake_validate)

    result = runner.invoke(cli.app, ["name", str(repo), "--apply"])

    assert result.exit_code == 0
    assert seen["entries"] == 1
    assert seen["applied_count"] == 1
    assert "validation passed" in result.stdout


def test_name_fails_without_refactor_plan(repo: Path) -> None:
    (repo / ".refactor_plan" / "refactor_plan.json").unlink()

    result = runner.invoke(cli.app, ["name", str(repo)])

    assert result.exit_code == 1
    assert "missing .refactor_plan/refactor_plan.json" in result.output
