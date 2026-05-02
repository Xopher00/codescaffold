"""Tests for applicator/contract.py — import-linter config generation."""

from pathlib import Path
import shutil
import subprocess

import networkx as nx
import pytest

from refactor_plan.contracts.import_contracts import build_cluster_dag, emit_contract
from refactor_plan.interface.cluster_view import build_view
from refactor_plan.planning.planner import plan

FIXTURE_GRAPH = (
    Path(__file__).parent
    / "fixtures"
    / "messy_repo"
    / ".refactor_plan"
    / "graph.json"
)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "messy_repo"


@pytest.fixture(scope="module")
def view():
    return build_view(FIXTURE_GRAPH)


@pytest.fixture(scope="module")
def refactor_plan(view):
    return plan(view, FIXTURE_REPO)


# ---------------------------------------------------------------------------
# 1. build_cluster_dag returns DiGraph with >= 1 edge (fixture has cross-cluster edges)
# ---------------------------------------------------------------------------


def test_build_cluster_dag_returns_digraph(refactor_plan):
    dag = build_cluster_dag(refactor_plan, FIXTURE_GRAPH)
    assert isinstance(dag, nx.DiGraph)


def test_build_cluster_dag_has_cross_cluster_edges(refactor_plan):
    """Fixture has cross-cluster edges; DAG should have >= 1 edge."""
    dag = build_cluster_dag(refactor_plan, FIXTURE_GRAPH)
    assert dag.number_of_edges() >= 1


def test_build_cluster_dag_nodes_are_cluster_names(refactor_plan):
    """All nodes in DAG should be cluster names (pkg_NNN)."""
    dag = build_cluster_dag(refactor_plan, FIXTURE_GRAPH)
    expected_names = {c.name for c in refactor_plan.clusters}
    assert set(dag.nodes()) == expected_names


def test_build_cluster_dag_no_self_loops(refactor_plan):
    """DAG should not have self-loops (intra-cluster edges filtered out)."""
    dag = build_cluster_dag(refactor_plan, FIXTURE_GRAPH)
    assert len(list(nx.selfloop_edges(dag))) == 0


# ---------------------------------------------------------------------------
# 2. emit_contract returns dict with config_text, config_path, contracts
# ---------------------------------------------------------------------------


def test_emit_contract_returns_artifact(refactor_plan, tmp_path):
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )
    assert artifact.config_path == tmp_path / ".importlinter"
    assert len(artifact.config_text) > 0
    assert len(artifact.contracts) >= 0


def test_emit_contract_config_text_contains_importlinter_section(refactor_plan, tmp_path):
    """config_text should contain [importlinter] section."""
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )
    assert "[importlinter]" in artifact.config_text
    assert "root_package = messy_pkg" in artifact.config_text


def test_emit_contract_config_text_contains_contract_section(refactor_plan, tmp_path):
    """config_text should contain at least one [importlinter:contract:*] section."""
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )
    assert "[importlinter:contract:" in artifact.config_text


# ---------------------------------------------------------------------------
# 3. Layers contract (if acyclic, should be emitted)
# ---------------------------------------------------------------------------


def test_emit_contract_checks_dag_acyclicity(refactor_plan, tmp_path):
    """If cluster DAG is acyclic, emit layers contract."""
    dag = build_cluster_dag(refactor_plan, FIXTURE_GRAPH)
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )

    if nx.is_directed_acyclic_graph(dag):
        # Layers contract should be present.
        layer_contracts = [c for c in artifact.contracts if c.get("type") == "layers"]
        assert len(layer_contracts) > 0
        assert "[importlinter:contract:layers]" in artifact.config_text
        assert "type = layers" in artifact.config_text
    else:
        # If cyclic, layers should be omitted.
        layer_contracts = [c for c in artifact.contracts if c.get("type") == "layers"]
        assert len(layer_contracts) == 0


# ---------------------------------------------------------------------------
# 4. Acyclic siblings contract (always emitted for pkg_NNN packages)
# ---------------------------------------------------------------------------


def test_emit_contract_acyclic_siblings_present(refactor_plan, tmp_path):
    """acyclic_siblings contract should always be present (pkg_NNN structure)."""
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )

    acyclic_contracts = [c for c in artifact.contracts if c.get("type") == "acyclic_siblings"]
    assert len(acyclic_contracts) > 0
    assert "[importlinter:contract:acyclic_siblings]" in artifact.config_text
    assert "type = acyclic_siblings" in artifact.config_text


# ---------------------------------------------------------------------------
# 5. Written .importlinter file matches config_text
# ---------------------------------------------------------------------------


def test_emit_contract_writes_file(refactor_plan, tmp_path):
    """Verify that .importlinter is written to repo_root."""
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )

    config_file = tmp_path / ".importlinter"
    assert config_file.exists()
    assert config_file.read_text() == artifact.config_text


# ---------------------------------------------------------------------------
# 6. Smoke test: lint-imports can parse the config without crashing
# ---------------------------------------------------------------------------


def test_emit_contract_lint_imports_smoke_test(refactor_plan, tmp_path):
    """Smoke test: invoke lint-imports on generated config; should not crash with parse error."""
    # Copy fixture repo to tmp_path so that lint-imports can find the root_package.
    copy_dest = tmp_path / "test_repo"
    shutil.copytree(FIXTURE_REPO, copy_dest)

    # Emit contract in the copy.
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, copy_dest, root_package="messy_pkg"
    )

    # Run lint-imports on the config.
    config_path = copy_dest / ".importlinter"
    result = subprocess.run(
        ["lint-imports", "--config", str(config_path)],
        cwd=str(copy_dest),
        capture_output=True,
        text=True,
    )

    # We expect the command to either pass (exit 0) or report violations (exit 1),
    # but NOT crash with a parse error (exit code > 1).
    # Accept exit codes 0 and 1 as success for this smoke test.
    assert result.returncode in (0, 1), (
        f"lint-imports crashed: exit code {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# 7. Contract structure checks (optional but valuable)
# ---------------------------------------------------------------------------


def test_emit_contract_all_clusters_in_acyclic_siblings(refactor_plan, tmp_path):
    """All pkg_NNN clusters should appear in acyclic_siblings contract."""
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )

    acyclic_contracts = [c for c in artifact.contracts if c.get("type") == "acyclic_siblings"]
    assert len(acyclic_contracts) > 0

    contract = acyclic_contracts[0]
    expected_clusters = {c.name for c in refactor_plan.clusters if c.name.startswith("pkg_")}
    actual_modules = set(contract.get("modules", []))

    assert actual_modules == expected_clusters


def test_emit_contract_forbidden_uses_correct_format(refactor_plan, tmp_path):
    """Forbidden contracts should have source_modules and forbidden_modules keys."""
    artifact = emit_contract(
        refactor_plan, build_view(FIXTURE_GRAPH), FIXTURE_GRAPH, tmp_path, root_package="messy_pkg"
    )

    forbidden_contracts = [c for c in artifact.contracts if c.get("type") == "forbidden"]
    for contract in forbidden_contracts:
        assert "source_modules" in contract
        assert "forbidden_modules" in contract
        assert "name" in contract
