"""Tests for namer.py.

All tests mock the Anthropic client — no network calls, no API key required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from refactor_plan.cluster_view import build_view
from refactor_plan.namer import (
    RenameEntry,
    RenameMap,
    gather_context,
    name_clusters,
    write_rename_map,
)
from refactor_plan.planner import plan

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def view():
    return build_view(FIXTURE_GRAPH)


@pytest.fixture(scope="module")
def refactor_plan(view):
    return plan(view, FIXTURE_REPO)


# ---------------------------------------------------------------------------
# Helper: build a mock Anthropic client
# ---------------------------------------------------------------------------


def make_mock_client(rmap: RenameMap) -> MagicMock:
    """Return a MagicMock whose messages.parse() returns a response with
    parsed_output == rmap."""
    client = MagicMock()
    parsed_response = MagicMock()
    parsed_response.parsed_output = rmap
    client.messages.parse.return_value = parsed_response
    return client


# ---------------------------------------------------------------------------
# Test 1: gather_context — no subprocesses, returns non-empty string with pkg_001
# ---------------------------------------------------------------------------


def test_gather_context_no_subprocesses(refactor_plan, view):
    """gather_context with use_wiki=False, use_explain=False must not fork any
    subprocess and must return a non-empty string mentioning pkg_001."""
    ctx = gather_context(
        refactor_plan,
        view,
        FIXTURE_REPO,
        FIXTURE_GRAPH,
        use_wiki=False,
        use_explain=False,
    )
    assert isinstance(ctx, str)
    assert len(ctx) > 0
    assert "pkg_001" in ctx


# ---------------------------------------------------------------------------
# Test 2: name_clusters with mock client — check RenameMap + call args
# ---------------------------------------------------------------------------


def test_name_clusters_with_mock(refactor_plan, view):
    """name_clusters must:
    - return the RenameMap with 1 entry
    - call messages.parse with model="claude-opus-4-7"
    - call messages.parse with output_format=RenameMap
    - pass a system arg containing a dict with cache_control block
    """
    expected_map = RenameMap(
        entries=[
            RenameEntry(
                old_name="pkg_001",
                new_name="core",
                rationale="largest cluster, broad utilities",
            )
        ]
    )
    mock_client = make_mock_client(expected_map)

    result = name_clusters(
        refactor_plan,
        view,
        FIXTURE_REPO,
        FIXTURE_GRAPH,
        model="claude-opus-4-7",
        anthropic_client=mock_client,
        use_wiki=False,
        use_explain=False,
    )

    # Return value
    assert isinstance(result, RenameMap)
    assert len(result.entries) == 1
    assert result.entries[0].old_name == "pkg_001"
    assert result.entries[0].new_name == "core"

    # messages.parse was called exactly once
    mock_client.messages.parse.assert_called_once()
    _, kwargs = mock_client.messages.parse.call_args

    # model
    assert kwargs["model"] == "claude-opus-4-7"

    # output_format
    assert kwargs["output_format"] is RenameMap

    # system contains the cache_control block
    system = kwargs["system"]
    assert isinstance(system, list)
    assert len(system) >= 1
    first_block = system[0]
    assert "cache_control" in first_block
    assert first_block["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Test 3: write_rename_map round-trip
# ---------------------------------------------------------------------------


def test_write_rename_map_round_trip(tmp_path):
    """write_rename_map must serialize to JSON that can be read back via
    RenameMap.model_validate_json and be equal to the original."""
    original = RenameMap(
        entries=[
            RenameEntry(old_name="pkg_001", new_name="core", rationale="biggest"),
            RenameEntry(old_name="pkg_002", new_name="io", rationale="file i/o"),
        ]
    )
    out = tmp_path / "rename_map.json"
    write_rename_map(original, out)

    assert out.exists()
    recovered = RenameMap.model_validate_json(out.read_text())
    assert recovered.model_dump() == original.model_dump()


# ---------------------------------------------------------------------------
# Test 4: Empty plan → empty RenameMap returned
# ---------------------------------------------------------------------------


def test_empty_plan_returns_empty_rename_map(view):
    """When the mock returns an empty RenameMap, name_clusters must return it
    unchanged."""
    from refactor_plan.planner import RefactorPlan

    empty_plan = RefactorPlan(
        clusters=[],
        file_moves=[],
        symbol_moves=[],
        shim_candidates=[],
        splitting_candidates=[],
    )

    empty_map = RenameMap(entries=[])
    mock_client = make_mock_client(empty_map)

    result = name_clusters(
        empty_plan,
        view,
        FIXTURE_REPO,
        FIXTURE_GRAPH,
        model="claude-opus-4-7",
        anthropic_client=mock_client,
        use_wiki=False,
        use_explain=False,
    )

    assert isinstance(result, RenameMap)
    assert result.entries == []
    mock_client.messages.parse.assert_called_once()
