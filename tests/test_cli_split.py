from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import refactor_plan.cli as cli
from refactor_plan.applicator.rope_runner import AppliedAction, ApplyResult
from refactor_plan.splitter import SplitPlan, SymbolSplit

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    fixture_src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(fixture_src, dst)
    return dst


def _split_plan(approved: bool = False) -> SplitPlan:
    return SplitPlan(
        splits=[
            SymbolSplit(
                symbol_id="sym",
                label="split_me()",
                source_file="pkg/source.py",
                source_community=1,
                target_community=2,
                dest_pkg="pkg_002",
                dest_mod="mod_001.py",
                rationale="test split",
                score=0.1,
                approved=approved,
            )
        ],
        triggers=[],
    )


def test_split_writes_split_plan(repo: Path) -> None:
    out = repo / ".refactor_plan" / "split_plan.json"
    out.unlink(missing_ok=True)

    result = runner.invoke(cli.app, ["split", str(repo)])

    assert result.exit_code == 0
    assert "wrote split_plan.json" in result.stdout
    assert out.exists()
    SplitPlan.model_validate_json(out.read_text())


def test_split_apply_loads_existing_plan_and_runs_validator(
    repo: Path,
    monkeypatch,
) -> None:
    out = repo / ".refactor_plan" / "split_plan.json"
    out.write_text(SplitPlan(splits=[], triggers=[]).model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_split_plan(plan, repo_root, *, only_approved, source_map=None):
        seen["only_approved"] = only_approved
        return ApplyResult(
            applied=[
                AppliedAction(kind="symbol_move", description="move", history_index=3)
            ],
            escalations=[],
        )

    def fake_validate(repo_root, applied_count, **kwargs):
        seen["applied_count"] = applied_count
        return SimpleNamespace(passed=True, rolled_back=False)

    monkeypatch.setattr(cli, "apply_split_plan", fake_apply_split_plan)
    monkeypatch.setattr(cli, "validate", fake_validate)

    result = runner.invoke(cli.app, ["split", str(repo), "--apply"])

    assert result.exit_code == 0
    assert seen["only_approved"] is True
    assert seen["applied_count"] == 1
    assert "validation passed" in result.stdout


def test_split_apply_validator_failure_exits_one(repo: Path, monkeypatch) -> None:
    out = repo / ".refactor_plan" / "split_plan.json"
    out.write_text(SplitPlan(splits=[], triggers=[]).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "apply_split_plan",
        lambda plan, repo_root, **kwargs: ApplyResult(
            applied=[
                AppliedAction(kind="symbol_move", description="move", history_index=1)
            ],
            escalations=[],
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate",
        lambda repo_root, applied_count, **kwargs: SimpleNamespace(
            passed=False,
            rolled_back=True,
        ),
    )

    result = runner.invoke(cli.app, ["split", str(repo), "--apply"])

    assert result.exit_code == 1
    assert "validation failed" in result.output


def test_split_apply_approve_splits_marks_plan_before_applying(
    repo: Path,
    monkeypatch,
) -> None:
    out = repo / ".refactor_plan" / "split_plan.json"
    out.write_text(_split_plan().model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_split_plan(plan, repo_root, *, only_approved, source_map=None):
        seen["approved"] = plan.splits[0].approved
        return ApplyResult(
            applied=[
                AppliedAction(kind="symbol_move", description="move", history_index=1)
            ],
            escalations=[],
        )

    monkeypatch.setattr(cli, "apply_split_plan", fake_apply_split_plan)
    monkeypatch.setattr(
        cli,
        "validate",
        lambda repo_root, applied_count, **kwargs: SimpleNamespace(
            passed=True,
            rolled_back=False,
        ),
    )

    result = runner.invoke(cli.app, ["split", str(repo), "--apply", "--approve-splits"])

    assert result.exit_code == 0
    assert seen["approved"] is True
    saved = SplitPlan.model_validate_json(out.read_text())
    assert saved.splits[0].approved is True
    assert "approved 1 split moves" in result.stdout


def test_split_apply_review_splits_can_decline_move(
    repo: Path,
    monkeypatch,
) -> None:
    out = repo / ".refactor_plan" / "split_plan.json"
    out.write_text(_split_plan().model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_split_plan(plan, repo_root, *, only_approved, source_map=None):
        seen["approved"] = plan.splits[0].approved
        return ApplyResult(
            applied=[
                AppliedAction(kind="symbol_move", description="move", history_index=1)
            ],
            escalations=[],
        )

    monkeypatch.setattr(cli, "apply_split_plan", fake_apply_split_plan)
    monkeypatch.setattr(
        cli,
        "validate",
        lambda repo_root, applied_count, **kwargs: SimpleNamespace(
            passed=True,
            rolled_back=False,
        ),
    )

    result = runner.invoke(
        cli.app,
        ["split", str(repo), "--apply", "--review-splits"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert seen["approved"] is False
    assert "Approve this split move?" in result.stdout
    assert "approved 0 split moves" in result.stdout
