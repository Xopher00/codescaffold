"""Tests for the grimp-based build_package_dag, _prepend_syspath, and drift guard."""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import pytest

from codescaffold.contracts.cycles import detect_package_cycles
from codescaffold.contracts.models import CycleReport
from codescaffold.contracts.package_graph import _prepend_syspath, build_package_dag
from codescaffold.graphify.snapshot import GraphSnapshot


FIXTURES = Path(__file__).parent / "fixtures" / "grimp_repos"


class TestBuildPackageDagFixtures:
    def test_simple_edge_direction(self):
        dag = build_package_dag(FIXTURES / "simple")
        assert "simplepkg.high" in dag.nodes
        assert "simplepkg.low" in dag.nodes
        assert dag.has_edge("simplepkg.high", "simplepkg.low")

    def test_subpackages_squash(self):
        dag = build_package_dag(FIXTURES / "subpackages")
        assert "mypkg.sub" in dag.nodes
        assert "mypkg.top" in dag.nodes
        assert dag.has_edge("mypkg.top", "mypkg.sub")
        assert "mypkg.sub.leaf" not in dag.nodes

    def test_external_imports_excluded(self):
        dag = build_package_dag(FIXTURES / "with_external")
        assert "networkx" not in dag.nodes
        assert not any(n.startswith("networkx") for n in dag.nodes)

    def test_cyclic_detection(self):
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        cycles = detect_package_cycles(FIXTURES / "cyclic", snap)
        assert len(cycles) >= 1
        assert isinstance(cycles[0], CycleReport)
        all_pkgs = {pkg for cr in cycles for pkg in cr.cycle}
        assert "cycpkg.mod_a" in all_pkgs or "cycpkg.mod_b" in all_pkgs


class TestSyspathRestored:
    def test_syspath_restored_on_exception(self, tmp_path: Path):
        before = list(sys.path)
        try:
            with _prepend_syspath(tmp_path / "nonexistent"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert sys.path == before

    def test_syspath_clean_after_normal_exit(self, tmp_path: Path):
        before = list(sys.path)
        with _prepend_syspath(tmp_path / "pkg"):
            pass
        assert sys.path == before


class TestGrimpAgreesWithLintImports:
    def test_grimp_agrees_with_lint_imports(self):
        """Grimp cycle detection and lint-imports must agree for the codescaffold repo."""
        from codescaffold.contracts.validator import run_lint_imports

        repo = Path(__file__).parent.parent
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        cycles = detect_package_cycles(repo, snap)
        result = run_lint_imports(repo)
        assert cycles == [], f"grimp detected cycles: {cycles}"
        assert result.succeeded, f"lint-imports failed: {result.raw_output}"
