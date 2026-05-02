from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from refactor_plan.interface.cluster_view import ClusterView
from refactor_plan.planning.planner import RefactorPlan

_HEADER = "[importlinter]\nroot_packages =\n    {root_package}\n"
_CONTRACT = (
    "\n[importlinter:contract:cluster_independence]\n"
    "name = cluster_independence\n"
    "type = independence\n"
    "modules =\n{modules}\n"
)


class ContractArtifact(BaseModel):
    config_path: str
    contracts: list[str]


def emit_contract(
    refactor_plan: RefactorPlan,
    view: ClusterView,
    graph_json: Path,
    repo_root: Path,
    root_package: str = "refactor_plan",
) -> ContractArtifact:
    module_lines: list[str] = []
    for cluster in refactor_plan.clusters:
        if not cluster.proposed_package:
            continue
        pkg_dir = Path(cluster.proposed_package)
        try:
            rel = pkg_dir.relative_to(repo_root / "src")
        except ValueError:
            continue
        module_lines.append(f"    {str(rel).replace('/', '.')}")

    contracts: list[str] = []
    content = _HEADER.format(root_package=root_package)
    if module_lines:
        contract = _CONTRACT.format(modules="\n".join(module_lines))
        content += contract
        contracts.append(contract)

    config_path = repo_root / ".importlinter"
    config_path.write_text(content, encoding="utf-8")

    return ContractArtifact(config_path=str(config_path), contracts=contracts)
