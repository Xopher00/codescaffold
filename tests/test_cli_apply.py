from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import refactor_plan.cli as cli
from refactor_plan.applicator.rope_runner import AppliedAction, ApplyResult
from refactor_plan.planner import RefactorPlan, SymbolMove
from typer.testing import CliRunner

runner = CliRunner()


def _write_empty_plan(repo: Path) -> None:
    out = repo / ".refactor_plan" / "refactor_plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    plan = RefactorPlan(
        clusters=[],
        file_moves=[],
        symbol_moves=[],
        shim_candidates=[],
        splitting_candidates=[],
    )
    out.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def _write_symbol_plan(repo: Path) -> None:
    out = repo / ".refactor_plan" / "refactor_plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    plan = RefactorPlan(
        clusters=[],
        file_moves=[],
        symbol_moves=[
            SymbolMove(
                symbol_id="sym",
                label="move_me()",
                src_file="pkg/source.py",
                dest_cluster="pkg_002",
                dest_file="pkg_002/mod_001.py",
                host_community=1,
                target_community=2,
            )
        ],
        shim_candidates=[],
        splitting_candidates=[],
    )
    out.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def _apply_result() -> ApplyResult:
    return ApplyResult(
        applied=[
            AppliedAction(kind="file_move", description="rope", history_index=1),
            AppliedAction(kind="residue_delete", description="pathlib", history_index=-1),
        ],
        escalations=[],
    )


def test_apply_fails_without_refactor_plan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(cli.app, ["apply", str(repo)])

    assert result.exit_code == 1
    assert "missing .refactor_plan/refactor_plan.json" in result.output


def test_apply_calls_apply_plan_for_approved_symbols(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_empty_plan(tmp_path)
    seen: dict[str, object] = {}

    def fake_apply_plan(plan, repo_root, *, only_approved_symbols):
        seen["only_approved_symbols"] = only_approved_symbols
        seen["repo_root"] = repo_root
        return _apply_result()

    def fake_validate(repo_root, applied_count, **kwargs):
        seen["applied_count"] = applied_count
        seen["escalations"] = kwargs["escalations"]
        return SimpleNamespace(passed=True, rolled_back=False)

    monkeypatch.setattr(cli, "apply_plan", fake_apply_plan)
    monkeypatch.setattr(cli, "validate", fake_validate)

    result = runner.invoke(cli.app, ["apply", str(tmp_path)])

    assert result.exit_code == 0
    assert seen["only_approved_symbols"] is True
    assert seen["repo_root"] == tmp_path
    assert seen["applied_count"] == 1
    assert "validation passed" in result.stdout


def test_apply_validator_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    _write_empty_plan(tmp_path)
    seen: dict[str, object] = {}

    monkeypatch.setattr(cli, "apply_plan", lambda plan, repo_root, **kwargs: _apply_result())

    def fake_validate(repo_root, applied_count, **kwargs):
        seen["validated_count"] = applied_count
        return SimpleNamespace(passed=False, rolled_back=False)

    def fake_rollback(repo_root, applied_count):
        seen["rollback"] = (repo_root, applied_count)

    monkeypatch.setattr(cli, "validate", fake_validate)
    monkeypatch.setattr(cli, "rollback", fake_rollback)

    result = runner.invoke(cli.app, ["apply", str(tmp_path)])

    assert result.exit_code == 1
    assert seen["validated_count"] == 1
    assert seen["rollback"] == (tmp_path, 1)
    assert "validation failed" in result.output


def test_apply_does_not_double_rollback_when_validator_did_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_empty_plan(tmp_path)
    seen = {"rollback_calls": 0}
    monkeypatch.setattr(cli, "apply_plan", lambda plan, repo_root, **kwargs: _apply_result())
    monkeypatch.setattr(
        cli,
        "validate",
        lambda repo_root, applied_count, **kwargs: SimpleNamespace(
            passed=False,
            rolled_back=True,
        ),
    )

    def fake_rollback(repo_root, applied_count):
        seen["rollback_calls"] += 1

    monkeypatch.setattr(cli, "rollback", fake_rollback)

    result = runner.invoke(cli.app, ["apply", str(tmp_path)])

    assert result.exit_code == 1
    assert seen["rollback_calls"] == 0


def test_apply_approve_symbols_marks_plan_before_applying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_symbol_plan(tmp_path)
    seen: dict[str, object] = {}

    def fake_apply_plan(plan, repo_root, *, only_approved_symbols):
        seen["approved"] = plan.symbol_moves[0].approved
        return _apply_result()

    monkeypatch.setattr(cli, "apply_plan", fake_apply_plan)
    monkeypatch.setattr(
        cli,
        "validate",
        lambda repo_root, applied_count, **kwargs: SimpleNamespace(
            passed=True,
            rolled_back=False,
        ),
    )

    result = runner.invoke(cli.app, ["apply", str(tmp_path), "--approve-symbols"])

    assert result.exit_code == 0
    assert seen["approved"] is True
    saved = RefactorPlan.model_validate_json(
        (tmp_path / ".refactor_plan" / "refactor_plan.json").read_text()
    )
    assert saved.symbol_moves[0].approved is True
    assert "approved 1 symbol moves" in result.stdout


def test_apply_review_symbols_can_decline_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_symbol_plan(tmp_path)
    seen: dict[str, object] = {}

    def fake_apply_plan(plan, repo_root, *, only_approved_symbols):
        seen["approved"] = plan.symbol_moves[0].approved
        return _apply_result()

    monkeypatch.setattr(cli, "apply_plan", fake_apply_plan)
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
        ["apply", str(tmp_path), "--review-symbols"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert seen["approved"] is False
    assert "Approve this symbol move?" in result.stdout
    assert "approved 0 symbol moves" in result.stdout


def test_apply_rejects_conflicting_approval_modes(tmp_path: Path) -> None:
    _write_empty_plan(tmp_path)

    result = runner.invoke(
        cli.app,
        ["apply", str(tmp_path), "--approve-symbols", "--review-symbols"],
    )

    assert result.exit_code == 1
    assert "choose only one" in result.output
