"""Tests for cleaner.py — E4 dead-code eliminator."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from refactor_plan.applicator.rope_runner import rollback
from refactor_plan.cleaner import (
    DeadCodeReport,
    DeadSymbol,
    apply_dead_code_report,
    build_dead_code_report,
)
from refactor_plan.cluster_view import FileCluster, GraphView

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_view(suggested_questions: list[dict]) -> GraphView:
    return GraphView(
        file_clusters=[FileCluster(id=0, files=["pkg/mod.py"], cohesion=0.5)],
        misplaced_symbols=[],
        god_nodes=[],
        surprising_connections=[],
        suggested_questions=suggested_questions,
        community_cohesion={0: 0.5},
    )


def _base_graph(tmp_path: Path, source_file: str = "pkg/mod.py") -> nx.Graph:
    """Graph with one isolated node (degree 0) and a real source file."""
    G = nx.Graph()
    G.add_node(
        "dead_sym",
        label="dead_func()",
        source_file=source_file,
        source_location="L10",
        community=0,
    )
    return G


# ---------------------------------------------------------------------------
# Test 1: no trigger → empty report
# ---------------------------------------------------------------------------


def test_no_trigger_returns_empty_report(tmp_path):
    """No isolated_nodes in suggested_questions → empty report."""
    view = _make_view([{"type": "bridge_node", "question": "q", "why": "w"}])
    G = _base_graph(tmp_path)

    report = build_dead_code_report(view, G, tmp_path)
    assert report.symbols == []


# ---------------------------------------------------------------------------
# Test 2: detect dead symbol
# ---------------------------------------------------------------------------


def test_detect_dead_symbol(tmp_path):
    """Isolated class node (degree 0, label without '()') → in report.

    We use a class label (no trailing '()') to avoid the _is_file_node heuristic
    which classifies degree-≤1 nodes with '()' labels as file stubs.
    """
    view = _make_view([{"type": "isolated_nodes", "question": "q", "why": "w"}])
    G = nx.Graph()
    # A dead class: degree 0, label without '()' → passes _is_file_node check
    G.add_node(
        "dead_sym",
        label="DeadHelper",
        source_file="pkg/mod.py",
        source_location="L5",
        community=0,
    )

    report = build_dead_code_report(view, G, tmp_path)

    assert len(report.symbols) == 1
    sym = report.symbols[0]
    assert sym.node_id == "dead_sym"
    assert sym.label == "DeadHelper"
    assert "EXTRACTED incoming" in sym.edge_context
    assert "INFERRED edges excluded" in sym.edge_context


# ---------------------------------------------------------------------------
# Test 3: symbol in __all__ excluded
# ---------------------------------------------------------------------------


def test_excludes_all_listed(tmp_path):
    """Symbol whose name appears in an __init__.py __all__ → not in report."""
    # Create a minimal package structure with __all__
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text('__all__ = ["DeadHelper"]\n')
    (pkg_dir / "mod.py").write_text("class DeadHelper: pass\n")

    view = _make_view([{"type": "isolated_nodes", "question": "q", "why": "w"}])
    G = nx.Graph()
    G.add_node(
        "dead_sym",
        label="DeadHelper",
        source_file="mypkg/mod.py",
        source_location="L1",
        community=0,
    )

    report = build_dead_code_report(view, G, tmp_path)

    ids = {s.node_id for s in report.symbols}
    assert "dead_sym" not in ids, "Symbol in __all__ should be excluded"


# ---------------------------------------------------------------------------
# Test 4: symbol in pyproject.toml scripts excluded
# ---------------------------------------------------------------------------


def test_excludes_pyproject_scripts(tmp_path):
    """Symbol named in [project.scripts] → not in report."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n\n[project.scripts]\nmycli = "mypkg.mod:DeadHelper"\n'
    )
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text("class DeadHelper: pass\n")

    view = _make_view([{"type": "isolated_nodes", "question": "q", "why": "w"}])
    G = nx.Graph()
    G.add_node(
        "dead_sym",
        label="DeadHelper",
        source_file="mypkg/mod.py",
        source_location="L1",
        community=0,
    )

    report = build_dead_code_report(view, G, tmp_path)

    ids = {s.node_id for s in report.symbols}
    assert "dead_sym" not in ids, "Symbol in pyproject.toml scripts should be excluded"


# ---------------------------------------------------------------------------
# Test 5: class methods excluded
# ---------------------------------------------------------------------------


def test_excludes_class_methods(tmp_path):
    """Symbols whose label starts with '.' (class methods) → not in report."""
    view = _make_view([{"type": "isolated_nodes", "question": "q", "why": "w"}])
    G = nx.Graph()
    G.add_node(
        "cls_method",
        label=".dead_method()",
        source_file="pkg/mod.py",
        source_location="L20",
        community=0,
    )

    report = build_dead_code_report(view, G, tmp_path)

    ids = {s.node_id for s in report.symbols}
    assert "cls_method" not in ids, "Class methods (label starts with '.') should be excluded"


# ---------------------------------------------------------------------------
# Test 6: apply without confirmed=True raises
# ---------------------------------------------------------------------------


def test_apply_without_confirmed_raises(tmp_path):
    """apply_dead_code_report with confirmed=False must raise ValueError."""
    report = DeadCodeReport(symbols=[
        DeadSymbol(
            node_id="x",
            label="dead_func()",
            source_file="mod.py",
            source_location="L1",
            rationale="test",
            edge_context="0 EXTRACTED incoming, 0 INFERRED edges excluded",
            approved=True,
        )
    ])

    with pytest.raises(ValueError, match="confirmed=True"):
        apply_dead_code_report(report, tmp_path, confirmed=False)


# ---------------------------------------------------------------------------
# Test 7: apply_dead_code_report rollback restores original file content
# ---------------------------------------------------------------------------


def test_dead_code_apply_rollback_restores_files(tmp_path):
    """Fix 5 — rollback() after apply_dead_code_report restores deleted symbol.

    Sequence:
    1. Write a Python source file with a dead function.
    2. Apply a dead-code report (confirmed=True, approved=True).
    3. Verify the function is removed from the file.
    4. Call rollback(repo_root, applied_count).
    5. Verify the original file content is restored.
    """
    # Arrange: create a minimal package with a dead function
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").touch()
    src_rel = "mypkg/mod.py"
    src_path = tmp_path / src_rel
    original_content = (
        "def dead_func():\n"
        "    pass\n"
        "\n"
        "def live_func():\n"
        "    return 42\n"
    )
    src_path.write_text(original_content, encoding="utf-8")

    report = DeadCodeReport(symbols=[
        DeadSymbol(
            node_id="dead_sym",
            label="dead_func()",
            source_file=src_rel,
            source_location="L1",
            rationale="degree=0, no incoming edges",
            edge_context="0 EXTRACTED incoming, 0 INFERRED edges excluded",
            approved=True,
        )
    ])

    # Act: apply the dead-code deletion
    result = apply_dead_code_report(report, tmp_path, confirmed=True)

    # Verify the symbol was deleted
    after_apply = src_path.read_text(encoding="utf-8")
    assert "dead_func" not in after_apply, "dead_func should have been removed"
    assert "live_func" in after_apply, "live_func must remain"

    # Act: rollback using the number of rope-tracked actions
    rope_count = sum(
        1 for a in result.applied if a.history_index != -1
    )
    rollback(tmp_path, rope_count)

    # Assert: original file content is restored
    after_rollback = src_path.read_text(encoding="utf-8")
    assert "dead_func" in after_rollback, (
        "rollback() must restore the file containing the deleted symbol"
    )
    assert after_rollback == original_content, (
        "rollback() must restore the exact original file content"
    )
