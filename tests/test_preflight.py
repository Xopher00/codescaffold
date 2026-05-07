"""Tests for codescaffold.bridge preflight resolution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from codescaffold.bridge.preflight import preflight_status, resolve_candidate, resolve_candidates
from codescaffold.bridge.resolution import RopeResolution
from codescaffold.candidates.models import MoveCandidate
from codescaffold.operations.results import SymbolInfo


FIXTURES = Path(__file__).parent / "fixtures" / "preflight_repo"


@pytest.fixture()
def preflight_repo(tmp_path: Path) -> Path:
    """Git-initialised copy of tests/fixtures/preflight_repo/ for rope."""
    shutil.copytree(FIXTURES, tmp_path / "preflight_repo")
    repo = tmp_path / "preflight_repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
    )
    return repo


def _candidate(
    symbol: str | None,
    source_file: str = "sample.py",
    target_file: str = "dest.py",
    kind: Literal["symbol", "module"] = "symbol",
) -> MoveCandidate:
    return MoveCandidate(
        kind=kind,
        source_file=source_file,
        symbol=symbol,
        target_file=target_file,
        community_id=0,
        reasons=("test",),
        confidence="medium",
    )


_SYMBOLS = [
    SymbolInfo(name="FooBuilder", type="class", line=1, col_offset=0, byte_offset=0),
    SymbolInfo(name="Helper", type="class", line=5, col_offset=0, byte_offset=10),
    SymbolInfo(name="standalone_fn", type="function", line=9, col_offset=0, byte_offset=20),
]


def _ls(symbols=_SYMBOLS):
    return patch("codescaffold.bridge.preflight.list_symbols", return_value=symbols)


def _close():
    return patch("codescaffold.bridge.preflight.close_rope_project")


# ---------------------------------------------------------------------------
# preflight_status mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("resolved", "ready"),
    ("ambiguous", "needs_review"),
    ("not_found", "needs_review"),
    ("not_top_level", "blocked"),
    ("not_python", "blocked"),
    ("error", "blocked"),
])
def test_preflight_status_mapping(status: str, expected: str) -> None:
    assert preflight_status(RopeResolution(status=status)) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_candidate — unit tests (mocked rope)
# ---------------------------------------------------------------------------

def test_resolved(tmp_path: Path) -> None:
    with _ls(), _close():
        res = resolve_candidate(_candidate("FooBuilder"), tmp_path)
    assert res.status == "resolved"
    assert res.symbol_kind == "class"
    assert res.line == 1


def test_not_found_with_near_misses(tmp_path: Path) -> None:
    with _ls(), _close():
        res = resolve_candidate(_candidate("Halper"), tmp_path)
    assert res.status == "not_found"
    assert "Helper" in res.near_misses


def test_not_found_no_near_misses(tmp_path: Path) -> None:
    with _ls(), _close():
        res = resolve_candidate(_candidate("ZZZUnrelated"), tmp_path)
    assert res.status == "not_found"
    assert res.near_misses == ()


def test_not_python(tmp_path: Path) -> None:
    with _close():
        res = resolve_candidate(_candidate("Anything", source_file="README.md"), tmp_path)
    assert res.status == "not_python"


def test_ambiguous(tmp_path: Path) -> None:
    dupes = [
        SymbolInfo(name="Dupe", type="class", line=1, col_offset=0, byte_offset=0),
        SymbolInfo(name="Dupe", type="function", line=5, col_offset=0, byte_offset=10),
    ]
    with _ls(dupes), _close():
        res = resolve_candidate(_candidate("Dupe"), tmp_path)
    assert res.status == "ambiguous"


def test_module_kind_always_resolved(tmp_path: Path) -> None:
    res = resolve_candidate(_candidate(None, kind="module"), tmp_path)
    assert res.status == "resolved"


# ---------------------------------------------------------------------------
# resolve_candidates — batching
# ---------------------------------------------------------------------------

def test_close_called_once(tmp_path: Path) -> None:
    candidates = [_candidate("FooBuilder"), _candidate("Helper")]
    with _ls() as mock_ls, patch("codescaffold.bridge.preflight.close_rope_project") as mock_close:
        results = resolve_candidates(candidates, tmp_path)
    assert len(results) == 2
    assert all(r.status == "resolved" for r in results)
    mock_close.assert_called_once_with(str(tmp_path))
    assert mock_ls.call_count == 1  # cached — same file not re-fetched


def test_close_called_on_error(tmp_path: Path) -> None:
    from codescaffold.operations.errors import RopeOperationError
    with patch("codescaffold.bridge.preflight.list_symbols", side_effect=RopeOperationError("ls", "fail", {})), \
         patch("codescaffold.bridge.preflight.close_rope_project") as mock_close:
        results = resolve_candidates([_candidate("FooBuilder")], tmp_path)
    assert results[0].status == "error"
    mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: real rope against fixture repo
# ---------------------------------------------------------------------------

def test_real_resolved(preflight_repo: Path) -> None:
    res = resolve_candidate(_candidate("FooBuilder"), preflight_repo)
    assert res.status == "resolved"
    assert res.symbol_kind == "class"


def test_real_not_found(preflight_repo: Path) -> None:
    res = resolve_candidate(_candidate("Halper"), preflight_repo)
    assert res.status == "not_found"


# ---------------------------------------------------------------------------
# approve_moves gate
# ---------------------------------------------------------------------------

def _write_plan(plan_dir: Path, plan) -> None:
    (plan_dir / ".refactor_plan").mkdir(exist_ok=True)
    (plan_dir / ".refactor_plan" / "refactor_plan.json").write_text(plan.model_dump_json())


def test_approve_blocks_blocked(tmp_path: Path) -> None:
    from codescaffold.mcp.tools import approve_moves
    from codescaffold.plans.schema import CandidateRecord, Plan, RopeResolutionRecord

    rec = CandidateRecord(
        kind="symbol", source_file="sample.py", symbol="Halper", target_file="dest.py",
        community_id=0, reasons=["test"], confidence="medium",
        resolution=RopeResolutionRecord(
            status="not_found", near_misses=["Helper"],
            reason="'Halper' not found in sample.py",
        ),
        preflight="blocked",
    )
    _write_plan(tmp_path, Plan(graph_hash="abc123", candidates=[rec]))

    with patch("codescaffold.mcp.tools.run_extract", return_value=MagicMock(graph_hash="abc123")), \
         patch("codescaffold.mcp.tools.assert_fresh"):
        result = approve_moves(
            [{"kind": "symbol", "source_file": "sample.py", "symbol": "Halper", "target_file": "dest.py"}],
            str(tmp_path),
        )

    assert "blocked" in result.lower()
    assert "Helper" in result


def test_approve_allows_ready(tmp_path: Path) -> None:
    from codescaffold.mcp.tools import approve_moves
    from codescaffold.plans.schema import CandidateRecord, Plan, RopeResolutionRecord

    rec = CandidateRecord(
        kind="symbol", source_file="sample.py", symbol="FooBuilder", target_file="dest.py",
        community_id=0, reasons=["test"], confidence="high",
        resolution=RopeResolutionRecord(status="resolved", symbol_kind="class", line=1),
        preflight="ready",
    )
    _write_plan(tmp_path, Plan(graph_hash="abc123", candidates=[rec]))

    with patch("codescaffold.mcp.tools.run_extract", return_value=MagicMock(graph_hash="abc123")), \
         patch("codescaffold.mcp.tools.assert_fresh"), \
         patch("codescaffold.mcp.tools.save"):
        result = approve_moves(
            [{"kind": "symbol", "source_file": "sample.py", "symbol": "FooBuilder", "target_file": "dest.py"}],
            str(tmp_path),
        )

    assert "Approved" in result
    assert "⚠" not in result


def test_approve_warns_needs_review(tmp_path: Path) -> None:
    from codescaffold.mcp.tools import approve_moves
    from codescaffold.plans.schema import CandidateRecord, Plan, RopeResolutionRecord

    rec = CandidateRecord(
        kind="symbol", source_file="sample.py", symbol="Dupe", target_file="dest.py",
        community_id=0, reasons=["test"], confidence="low",
        resolution=RopeResolutionRecord(status="ambiguous", reason="2 top-level symbols named 'Dupe'"),
        preflight="needs_review",
    )
    _write_plan(tmp_path, Plan(graph_hash="abc123", candidates=[rec]))

    with patch("codescaffold.mcp.tools.run_extract", return_value=MagicMock(graph_hash="abc123")), \
         patch("codescaffold.mcp.tools.assert_fresh"), \
         patch("codescaffold.mcp.tools.save"):
        result = approve_moves(
            [{"kind": "symbol", "source_file": "sample.py", "symbol": "Dupe", "target_file": "dest.py"}],
            str(tmp_path),
        )

    assert "Approved" in result
    assert "⚠" in result or "needs_review" in result


def test_approve_warns_handcrafted(tmp_path: Path) -> None:
    from codescaffold.mcp.tools import approve_moves
    from codescaffold.plans.schema import Plan

    _write_plan(tmp_path, Plan(graph_hash="abc123", candidates=[]))

    with patch("codescaffold.mcp.tools.run_extract", return_value=MagicMock(graph_hash="abc123")), \
         patch("codescaffold.mcp.tools.assert_fresh"), \
         patch("codescaffold.mcp.tools.save"):
        result = approve_moves(
            [{"kind": "symbol", "source_file": "sample.py", "symbol": "Anything", "target_file": "dest.py"}],
            str(tmp_path),
        )

    assert "Approved" in result
    assert "handcrafted" in result or "⚠" in result
