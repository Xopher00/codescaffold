from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import refactor_plan.cli as cli
from refactor_plan.applicator.rope_runner import AppliedAction, ApplyResult
from refactor_plan.cleaner import DeadCodeReport, DeadSymbol

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    fixture_src = Path(__file__).parent / "fixtures" / "messy_repo"
    dst = tmp_path / "messy_repo"
    shutil.copytree(fixture_src, dst)
    return dst


def _report(approved: bool = False) -> DeadCodeReport:
    return DeadCodeReport(
        symbols=[
            DeadSymbol(
                node_id="dead",
                label="unused()",
                source_file="pkg/mod.py",
                source_location="1:0",
                rationale="test",
                edge_context="0 EXTRACTED incoming, 0 INFERRED edges excluded",
                approved=approved,
            )
        ]
    )


def test_clean_writes_dead_code_report(repo: Path) -> None:
    out = repo / ".refactor_plan" / "dead_code_report.json"
    md = repo / ".refactor_plan" / "DEAD_CODE_REPORT.md"
    out.unlink(missing_ok=True)
    md.unlink(missing_ok=True)

    result = runner.invoke(cli.app, ["clean", str(repo)])

    assert result.exit_code == 0
    assert "wrote dead_code_report.json" in result.stdout
    assert out.exists()
    assert md.exists()
    DeadCodeReport.model_validate_json(out.read_text())


def test_clean_apply_without_confirmed_exits_one(repo: Path) -> None:
    result = runner.invoke(cli.app, ["clean", str(repo), "--apply"])

    assert result.exit_code == 1
    assert (
        "use --apply --confirmed to execute deletions; edit dead_code_report.json "
        "to set approved=True first"
    ) in result.output


def test_clean_apply_confirmed_uses_existing_report_and_validates(
    repo: Path,
    monkeypatch,
) -> None:
    report_path = repo / ".refactor_plan" / "dead_code_report.json"
    report_path.write_text(_report(approved=True).model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_dead_code_report(report, repo_root, *, confirmed, source_map=None):
        seen["approved"] = report.symbols[0].approved
        seen["confirmed"] = confirmed
        return ApplyResult(
            applied=[
                AppliedAction(kind="organize_imports", description="rope", history_index=4)
            ],
            escalations=[],
        )

    def fake_validate(repo_root, applied_count, **kwargs):
        seen["applied_count"] = applied_count
        return SimpleNamespace(passed=True, rolled_back=False)

    monkeypatch.setattr(cli, "apply_dead_code_report", fake_apply_dead_code_report)
    monkeypatch.setattr(cli, "validate", fake_validate)

    result = runner.invoke(cli.app, ["clean", str(repo), "--apply", "--confirmed"])

    assert result.exit_code == 0
    assert seen["approved"] is True
    assert seen["confirmed"] is True
    assert seen["applied_count"] == 1


def test_clean_apply_confirmed_validator_failure_exits_one(
    repo: Path,
    monkeypatch,
) -> None:
    report_path = repo / ".refactor_plan" / "dead_code_report.json"
    report_path.write_text(_report(approved=True).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "apply_dead_code_report",
        lambda report, repo_root, **kwargs: ApplyResult(
            applied=[
                AppliedAction(kind="organize_imports", description="rope", history_index=1)
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

    result = runner.invoke(cli.app, ["clean", str(repo), "--apply", "--confirmed"])

    assert result.exit_code == 1
    assert "validation failed" in result.output


def test_clean_apply_confirmed_approve_deletions_marks_report(
    repo: Path,
    monkeypatch,
) -> None:
    report_path = repo / ".refactor_plan" / "dead_code_report.json"
    report_path.write_text(_report(approved=False).model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_dead_code_report(report, repo_root, *, confirmed, source_map=None):
        seen["approved"] = report.symbols[0].approved
        return ApplyResult(
            applied=[
                AppliedAction(kind="organize_imports", description="rope", history_index=1)
            ],
            escalations=[],
        )

    monkeypatch.setattr(cli, "apply_dead_code_report", fake_apply_dead_code_report)
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
        ["clean", str(repo), "--apply", "--confirmed", "--approve-deletions"],
    )

    assert result.exit_code == 0
    assert seen["approved"] is True
    saved = DeadCodeReport.model_validate_json(report_path.read_text())
    assert saved.symbols[0].approved is True
    assert "approved 1 deletions" in result.stdout


def test_clean_apply_confirmed_review_deletions_can_decline(
    repo: Path,
    monkeypatch,
) -> None:
    report_path = repo / ".refactor_plan" / "dead_code_report.json"
    report_path.write_text(_report(approved=False).model_dump_json(), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_apply_dead_code_report(report, repo_root, *, confirmed, source_map=None):
        seen["approved"] = report.symbols[0].approved
        return ApplyResult(
            applied=[
                AppliedAction(kind="organize_imports", description="rope", history_index=1)
            ],
            escalations=[],
        )

    monkeypatch.setattr(cli, "apply_dead_code_report", fake_apply_dead_code_report)
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
        ["clean", str(repo), "--apply", "--confirmed", "--review-deletions"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert seen["approved"] is False
    assert "Approve this deletion?" in result.stdout
    assert "approved 0 deletions" in result.stdout
