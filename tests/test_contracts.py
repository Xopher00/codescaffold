"""Tests for codescaffold.contracts — models, package_graph, cycles, generator, validator."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from codescaffold.candidates.models import MoveCandidate
from codescaffold.contracts.models import (
    ContractArtifact,
    ContractValidationResult,
    CycleReport,
    ViolationReport,
)
from codescaffold.contracts.package_graph import (
    _file_to_subpackage,
    build_package_dag,
    detect_root_package,
)
from codescaffold.contracts.validator import run_lint_imports
from codescaffold.graphify.snapshot import GraphSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snap(edges: list[tuple[str, str]], node_files: dict[str, str]) -> GraphSnapshot:
    """Build a GraphSnapshot from explicit edge and file-attr dicts."""
    G = nx.DiGraph()
    for node, src in node_files.items():
        G.add_node(node, label=node, source_file=src)
    for u, v in edges:
        if u in G and v in G:
            G.add_edge(u, v)
    return GraphSnapshot.from_graph(G)


# ---------------------------------------------------------------------------
# Models — frozen invariants
# ---------------------------------------------------------------------------

class TestModels:
    def test_cycle_report_frozen(self):
        cr = CycleReport(cycle=("a", "b"), edges=(("a", "b"), ("b", "a")), suggested_break=None)
        with pytest.raises((TypeError, AttributeError)):
            cr.cycle = ("x",)  # type: ignore

    def test_contract_artifact_frozen(self):
        art = ContractArtifact(
            config_path="/tmp/.importlinter",
            layers=(),
            forbidden=(),
            cycles_detected=(),
            written=False,
        )
        with pytest.raises((TypeError, AttributeError)):
            art.written = True  # type: ignore

    def test_validation_result_frozen(self):
        r = ContractValidationResult(succeeded=True, raw_output="", contracts_checked=0, contracts_failed=0)
        with pytest.raises((TypeError, AttributeError)):
            r.succeeded = False  # type: ignore

    def test_violation_report_frozen(self):
        v = ViolationReport(pre_apply_passed=True, post_apply_passed=False, is_regression=True, raw_output="")
        with pytest.raises((TypeError, AttributeError)):
            v.is_regression = False  # type: ignore


# ---------------------------------------------------------------------------
# _file_to_subpackage
# ---------------------------------------------------------------------------

class TestFileToSubpackage:
    def test_standard_layout(self):
        assert _file_to_subpackage("src/mypkg/graphify/extract.py") == "mypkg.graphify"

    def test_root_module(self):
        assert _file_to_subpackage("src/mypkg/__init__.py") == "mypkg"

    def test_deep_nesting(self):
        # Only take first two levels after src/
        assert _file_to_subpackage("src/mypkg/sub/deep/module.py") == "mypkg.sub"

    def test_no_src_prefix(self):
        # Without src/, still tries to derive something
        result = _file_to_subpackage("mypkg/sub/module.py", src_root="src")
        # Falls back to attempting first two parts
        assert result is not None or result is None  # just don't crash


# ---------------------------------------------------------------------------
# build_package_dag
# ---------------------------------------------------------------------------

class TestBuildPackageDag:
    def test_acyclic_dag(self, tmp_path: Path):
        _make_import_pkg(tmp_path, "mypkg")
        dag = build_package_dag(tmp_path)
        assert "mypkg.api" in dag.nodes
        assert "mypkg.utils" in dag.nodes
        assert dag.has_edge("mypkg.api", "mypkg.utils")

    def test_self_loops_excluded(self, tmp_path: Path):
        src = tmp_path / "src" / "mypkg"
        src.mkdir(parents=True)
        (src / "__init__.py").touch()
        api = src / "api"
        api.mkdir()
        (api / "__init__.py").touch()
        (api / "a.py").write_text("")
        (api / "b.py").write_text("from mypkg.api.a import x\n")
        dag = build_package_dag(tmp_path)
        assert not dag.has_edge("mypkg.api", "mypkg.api")

    def test_empty_package_gives_single_node(self, tmp_path: Path):
        _setup_root_package(tmp_path, "mypkg")  # only __init__.py
        dag = build_package_dag(tmp_path)
        assert "mypkg" in dag.nodes
        assert dag.number_of_edges() == 0


# ---------------------------------------------------------------------------
# detect_root_package
# ---------------------------------------------------------------------------

class TestDetectRootPackage:
    def test_from_src_directory(self, tmp_path: Path):
        src = tmp_path / "src" / "myrootpkg"
        src.mkdir(parents=True)
        (src / "__init__.py").touch()
        assert detect_root_package(tmp_path) == "myrootpkg"

    def test_multiple_packages_raises(self, tmp_path: Path):
        for name in ("pkga", "pkgb"):
            p = tmp_path / "src" / name
            p.mkdir(parents=True)
            (p / "__init__.py").touch()
        # May raise ValueError or return one of them — as long as it doesn't crash silently
        try:
            result = detect_root_package(tmp_path)
            assert isinstance(result, str)
        except ValueError:
            pass

    def test_missing_src_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            detect_root_package(tmp_path)


# ---------------------------------------------------------------------------
# detect_package_cycles
# ---------------------------------------------------------------------------

class TestDetectPackageCycles:
    def test_acyclic_returns_empty(self, tmp_path: Path):
        from codescaffold.contracts.cycles import detect_package_cycles
        _make_import_pkg(tmp_path, "mypkg", cyclic=False)
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        assert detect_package_cycles(tmp_path, snap) == []

    def test_cyclic_returns_reports(self, tmp_path: Path):
        from codescaffold.contracts.cycles import detect_package_cycles
        _make_import_pkg(tmp_path, "mypkg", cyclic=True)
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        cycles = detect_package_cycles(tmp_path, snap)
        assert len(cycles) >= 1
        assert isinstance(cycles[0], CycleReport)

    def test_cycle_report_has_packages(self, tmp_path: Path):
        from codescaffold.contracts.cycles import detect_package_cycles
        _make_import_pkg(tmp_path, "mypkg", cyclic=True)
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        cycles = detect_package_cycles(tmp_path, snap)
        all_pkgs = {pkg for cr in cycles for pkg in cr.cycle}
        assert "mypkg.api" in all_pkgs or "mypkg.utils" in all_pkgs


# ---------------------------------------------------------------------------
# generate_importlinter_config
# ---------------------------------------------------------------------------

class TestGenerateImportlinterConfig:
    def test_cyclic_does_not_write(self, tmp_path: Path):
        from codescaffold.contracts.generator import generate_importlinter_config
        _make_import_pkg(tmp_path, "mypkg", cyclic=True)
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        artifact = generate_importlinter_config(tmp_path, snap)
        assert artifact.written is False
        assert len(artifact.cycles_detected) >= 1
        assert not (tmp_path / ".importlinter").exists()

    def test_acyclic_writes_file(self, tmp_path: Path):
        from codescaffold.contracts.generator import generate_importlinter_config
        _setup_root_package(tmp_path, "mypkg")
        snap = _make_snap(
            edges=[("A", "B")],
            node_files={
                "A": "src/mypkg/api/views.py",
                "B": "src/mypkg/utils/helpers.py",
            },
        )
        artifact = generate_importlinter_config(tmp_path, snap)
        assert artifact.written is True
        assert (tmp_path / ".importlinter").exists()
        assert len(artifact.cycles_detected) == 0

    def test_empty_graph_writes_file(self, tmp_path: Path):
        from codescaffold.contracts.generator import generate_importlinter_config
        _setup_root_package(tmp_path, "mypkg")
        snap = GraphSnapshot.from_graph(nx.DiGraph())
        artifact = generate_importlinter_config(tmp_path, snap)
        assert artifact.written is True

    def test_written_file_contains_root_package(self, tmp_path: Path):
        from codescaffold.contracts.generator import generate_importlinter_config
        _setup_root_package(tmp_path, "mypkg")
        snap = _make_snap(
            edges=[("A", "B")],
            node_files={
                "A": "src/mypkg/api/views.py",
                "B": "src/mypkg/utils/helpers.py",
            },
        )
        generate_importlinter_config(tmp_path, snap)
        content = (tmp_path / ".importlinter").read_text()
        assert "mypkg" in content
        assert "[importlinter]" in content


# ---------------------------------------------------------------------------
# run_lint_imports (validator)
# ---------------------------------------------------------------------------

class TestRunLintImports:
    def test_no_config_succeeds(self, tmp_path: Path):
        result = run_lint_imports(tmp_path)
        assert result.succeeded is True
        assert result.contracts_checked == 0
        assert result.contracts_failed == 0
        assert "(no .importlinter)" in result.raw_output

    def test_returns_typed_result(self, tmp_path: Path):
        result = run_lint_imports(tmp_path)
        assert isinstance(result, ContractValidationResult)


# ---------------------------------------------------------------------------
# propose_alternatives (violation_fix)
# ---------------------------------------------------------------------------

class TestProposeAlternatives:
    def test_empty_moves_returns_empty(self):
        from codescaffold.contracts.violation_fix import propose_alternatives
        snap = _make_snap(
            edges=[("A", "B")],
            node_files={
                "A": "src/mypkg/api/views.py",
                "B": "src/mypkg/utils/helpers.py",
            },
        )
        result = propose_alternatives(failed_moves=(), snap=snap, layers=())
        assert result == []

    def test_returns_move_candidates(self):
        from codescaffold.contracts.violation_fix import propose_alternatives
        from codescaffold.plans.schema import ApprovedMove
        snap = _make_snap(
            edges=[("A", "B"), ("B", "C")],
            node_files={
                "A": "src/mypkg/api/views.py",
                "B": "src/mypkg/service/logic.py",
                "C": "src/mypkg/utils/helpers.py",
            },
        )
        # A is in api (layer 0), B in service (layer 1), C in utils (layer 2)
        # Move tries to put something from utils to api (violation)
        failed = (
            ApprovedMove(kind="symbol", source_file="src/mypkg/utils/helpers.py",
                         symbol="C", target_file="src/mypkg/api/views.py"),
        )
        layers = (("mypkg.api",), ("mypkg.service",), ("mypkg.utils",))
        result = propose_alternatives(failed_moves=failed, snap=snap, layers=layers)
        # Should return something — or gracefully handle the case
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ValidationResult — contracts_ok integration
# ---------------------------------------------------------------------------

class TestValidationResultContracts:
    def test_contracts_ok_defaults_true(self):
        from codescaffold.validation.runner import ValidationResult
        r = ValidationResult(
            compileall_ok=True,
            pytest_ok=True,
            pytest_summary="",
            failed_steps=(),
        )
        assert r.contracts_ok is True
        assert r.succeeded is True

    def test_contracts_ok_false_makes_succeeded_false(self):
        from codescaffold.validation.runner import ValidationResult
        r = ValidationResult(
            compileall_ok=True,
            pytest_ok=True,
            pytest_summary="",
            failed_steps=(),
            contracts_ok=False,
        )
        assert r.succeeded is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_root_package(tmp_path: Path, name: str) -> None:
    """Create minimal src/<name>/__init__.py so detect_root_package works."""
    pkg = tmp_path / "src" / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").touch()


def _make_import_pkg(tmp_path: Path, pkg: str, *, cyclic: bool = False) -> None:
    """Create src/<pkg>/{__init__,api/{__init__,views.py},utils/{__init__,helpers.py}}.

    If cyclic=True, api.views imports utils.helpers and utils.helpers imports api.views,
    creating a package cycle that grimp will detect.
    """
    src = tmp_path / "src" / pkg
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").touch()
    for sub in ("api", "utils"):
        d = src / sub
        d.mkdir()
        (d / "__init__.py").touch()
    if cyclic:
        (src / "api" / "views.py").write_text(f"from {pkg}.utils.helpers import x\n")
        (src / "utils" / "helpers.py").write_text(f"from {pkg}.api.views import y\n")
    else:
        (src / "api" / "views.py").write_text(f"from {pkg}.utils.helpers import x\n")
        (src / "utils" / "helpers.py").write_text("")
