"""E4 — Dead-code eliminator.

Triggered by graphify's `suggest_questions[type == isolated_nodes]`.
Re-derives the actual dead-symbol set via Part A signals only:
- degree <= 1 (undirected)
- zero incoming edges of relation in DEAD_RELATIONS (calls, imports_from, method)
  NOTE: the relation is `imports_from`, not `imports` — verified by enumerating
  messy_repo's graph.json.
- not in __all__ (planner._collect_all_exports)
- not in [project.scripts] / [project.entry-points.*] (planner._parse_pyproject_scripts)
- not a class method (label starts with '.')

No LLM calls in this module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import libcst as cst
import networkx as nx
from pydantic import BaseModel
from rope.base.project import Project
from rope.refactor.importutils import ImportOrganizer

from refactor_plan.applicator.rope_runner import (
    AppliedAction,
    ApplyResult,
    Escalation,
    _write_stray_deleted_manifest,
)
from refactor_plan.cluster_view import GraphView
from refactor_plan.planner import _collect_all_exports, _parse_pyproject_scripts
from graphify.analyze import _is_concept_node, _is_file_node

log = logging.getLogger(__name__)

# Part A relations only — dead-code signal is zero incoming of these.
# NOTE: the relation name in graphify output is `imports_from`, not `imports`.
DEAD_RELATIONS = {"calls", "imports_from", "method"}

_DEAD_CODE_REPORT_PATH = ".refactor_plan/dead_code_report.json"


class DeadSymbol(BaseModel):
    node_id: str
    label: str
    source_file: str
    source_location: str
    rationale: str
    edge_context: str   # e.g. "0 EXTRACTED incoming, 2 INFERRED edges excluded"
    approved: bool = False


class DeadCodeReport(BaseModel):
    symbols: list[DeadSymbol]


def _count_incoming_by_confidence(G: nx.Graph, node_id: str) -> dict[str, int]:
    """Count incoming edges of DEAD_RELATIONS grouped by confidence.

    Since the graph is undirected, 'incoming' is determined by checking
    `d.get("_tgt") == node_id` on every edge incident to node_id.
    """
    counts: dict[str, int] = {}
    for _, _, d in G.edges(node_id, data=True):
        if d.get("_tgt") == node_id and d.get("relation") in DEAD_RELATIONS:
            conf = d.get("confidence", "UNKNOWN")
            counts[conf] = counts.get(conf, 0) + 1
    return counts


def _has_incoming_dead_relation(G: nx.Graph, node_id: str) -> bool:
    """True iff node_id has at least one incoming EXTRACTED/INFERRED edge in DEAD_RELATIONS."""
    for _, _, d in G.edges(node_id, data=True):
        if d.get("_tgt") == node_id and d.get("relation") in DEAD_RELATIONS:
            conf = d.get("confidence", "")
            if conf in ("EXTRACTED", "INFERRED"):
                return True
    return False


def _build_edge_context(G: nx.Graph, node_id: str) -> str:
    """Build the INFERRED-edge context annotation for a dead symbol.

    Format: "N EXTRACTED incoming, M INFERRED edges excluded"
    where N is the count of incoming EXTRACTED edges of DEAD_RELATIONS
    (should be 0 by definition) and M is the count of INFERRED edges excluded.
    """
    by_conf = _count_incoming_by_confidence(G, node_id)
    extracted = by_conf.get("EXTRACTED", 0)
    inferred = by_conf.get("INFERRED", 0)
    return f"{extracted} EXTRACTED incoming, {inferred} INFERRED edges excluded"


def build_dead_code_report(
    view: GraphView,
    graph: nx.Graph,
    repo_root: Path,
) -> DeadCodeReport:
    """Build a DeadCodeReport from a GraphView.

    Trigger: presence of `isolated_nodes` entry in `view.suggested_questions`.
    If absent, returns an empty report.
    """
    has_trigger = any(
        q.get("type") == "isolated_nodes" for q in view.suggested_questions
    )
    if not has_trigger:
        return DeadCodeReport(symbols=[])

    all_exports = _collect_all_exports(repo_root)
    pyproject_refs = _parse_pyproject_scripts(repo_root)

    symbols: list[DeadSymbol] = []
    for node_id, data in graph.nodes(data=True):
        # Skip file nodes and concept nodes
        if _is_file_node(graph, node_id) or _is_concept_node(graph, node_id):
            continue

        # Skip rationale nodes (graphify internal)
        if "rationale" in node_id:
            continue

        # Degree filter (undirected): degree <= 1
        if graph.degree(node_id) > 1:
            continue

        label = data.get("label", node_id)
        source_file = data.get("source_file", "")
        source_location = data.get("source_location", "")

        # Skip class methods (label starts with '.')
        if label.startswith("."):
            continue

        # Skip if symbol is in __all__ of any __init__.py
        sym_name = label.rstrip("()").lstrip(".")
        if sym_name in all_exports:
            continue

        # Skip if symbol is referenced in pyproject.toml scripts/entry-points
        if any(sym_name in ref for ref in pyproject_refs):
            continue

        # Must have zero incoming EXTRACTED/INFERRED edges of DEAD_RELATIONS
        if _has_incoming_dead_relation(graph, node_id):
            continue

        edge_context = _build_edge_context(graph, node_id)

        rationale = (
            f"0 incoming + not exported, isolated_nodes signal present. "
            f"Degree={graph.degree(node_id)}."
        )

        symbols.append(
            DeadSymbol(
                node_id=node_id,
                label=label,
                source_file=source_file,
                source_location=source_location,
                rationale=rationale,
                edge_context=edge_context,
            )
        )

    # Sort for determinism
    symbols.sort(key=lambda s: (s.source_file, s.label))

    return DeadCodeReport(symbols=symbols)


def save_dead_code_report(report: DeadCodeReport, repo_root: Path) -> Path:
    """Save the DeadCodeReport to .refactor_plan/dead_code_report.json."""
    out_path = repo_root / _DEAD_CODE_REPORT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.model_dump(), indent=2), encoding="utf-8"
    )
    return out_path


def load_dead_code_report(repo_root: Path) -> DeadCodeReport:
    """Load a previously saved DeadCodeReport."""
    path = repo_root / _DEAD_CODE_REPORT_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    return DeadCodeReport(**data)


class _SymbolRemover(cst.CSTTransformer):
    """libCST transformer that removes a named FunctionDef or ClassDef from a module body.

    Uses RemovalSentinel.REMOVE — cleaner than rope Rename-to-sentinel approach.
    """

    def __init__(self, symbol_name: str) -> None:
        super().__init__()
        self.symbol_name = symbol_name
        self.removed = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.BaseStatement | cst.RemovalSentinel:
        if updated_node.name.value == self.symbol_name:
            self.removed = True
            return cst.RemovalSentinel.REMOVE
        return updated_node

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.BaseStatement | cst.RemovalSentinel:
        if updated_node.name.value == self.symbol_name:
            self.removed = True
            return cst.RemovalSentinel.REMOVE
        return updated_node


def apply_dead_code_report(
    report: DeadCodeReport,
    repo_root: Path,
    *,
    confirmed: bool = False,
) -> ApplyResult:
    """Apply approved dead-symbol deletions via libCST RemovalSentinel.

    Only acts when `confirmed=True` AND each entry has `approved=True`.
    Raises ValueError if `confirmed=False` so the CLI layer can surface it.

    After deletion, rope's organize_imports is run on touched files.
    Returns ApplyResult.
    """
    if not confirmed:
        raise ValueError(
            "apply_dead_code_report requires confirmed=True. "
            "Review dead_code_report.json, set approved=True on entries to delete, "
            "then re-run with --confirmed."
        )

    approved = [s for s in report.symbols if s.approved]
    applied: list[AppliedAction] = []
    escalations: list[Escalation] = []
    stray_deleted_files: dict[str, str] = {}  # {rel_path: original_content} for rollback

    if not approved:
        return ApplyResult(applied=applied, escalations=escalations)

    # Group by source file for batch processing
    by_file: dict[str, list[DeadSymbol]] = {}
    for sym in approved:
        by_file.setdefault(sym.source_file, []).append(sym)

    # Track which files we've already captured originals for (capture once per file)
    captured_originals: set[str] = set()

    touched_paths: list[Path] = []

    for src_rel, syms in sorted(by_file.items()):
        src_path = repo_root / src_rel
        if not src_path.exists():
            for sym in syms:
                escalations.append(
                    Escalation(
                        kind="no_referent",
                        symbol_id=sym.node_id,
                        detail=f"Source file '{src_rel}' not found at {src_path}",
                    )
                )
            continue

        # Capture original content before any edits (once per file) for rollback
        if src_rel not in captured_originals:
            captured_originals.add(src_rel)
            try:
                stray_deleted_files[src_rel] = src_path.read_text(encoding="utf-8")
            except OSError:
                stray_deleted_files[src_rel] = ""

        try:
            source = src_path.read_text(encoding="utf-8")
            tree = cst.parse_module(source)
        except Exception as exc:
            for sym in syms:
                escalations.append(
                    Escalation(
                        kind="move_error",
                        symbol_id=sym.node_id,
                        detail=f"Cannot parse {src_rel}: {exc}",
                    )
                )
            continue

        any_removed = False
        for sym in syms:
            sym_name = sym.label.rstrip("()").lstrip(".")
            remover = _SymbolRemover(sym_name)
            try:
                tree = tree.visit(remover)
                if remover.removed:
                    any_removed = True
                    applied.append(
                        AppliedAction(
                            kind="dead_code_delete",
                            description=f"Deleted dead symbol {sym.label} from {src_rel}",
                            history_index=-1,
                        )
                    )
                else:
                    escalations.append(
                        Escalation(
                            kind="offset_not_found",
                            symbol_id=sym.node_id,
                            detail=f"Symbol '{sym_name}' not found as FunctionDef/ClassDef in {src_rel}",
                        )
                    )
            except Exception as exc:
                escalations.append(
                    Escalation(
                        kind="move_error",
                        symbol_id=sym.node_id,
                        detail=f"libCST removal error for '{sym_name}' in {src_rel}: {exc}",
                    )
                )

        if any_removed:
            src_path.write_text(tree.code, encoding="utf-8")
            touched_paths.append(src_path)

    # Run organize_imports on touched files via rope
    if touched_paths:
        project = Project(str(repo_root))
        try:
            organizer = ImportOrganizer(project)
            from rope.base import libutils as _libutils
            for path in touched_paths:
                try:
                    resource = _libutils.path_to_resource(project, str(path))
                    if resource is not None:
                        changes = organizer.organize_imports(resource)
                        if changes is not None:
                            project.do(changes)
                            applied.append(
                                AppliedAction(
                                    kind="organize_imports",
                                    description=f"Organized imports in {path.relative_to(repo_root)}",
                                    history_index=len(project.history.undo_list),
                                )
                            )
                except Exception as exc:
                    log.debug("organize_imports skipped for %s: %s", path, exc)
        finally:
            project.close()

    # Write stray-deleted manifest so rollback() can restore original file content
    if stray_deleted_files:
        _write_stray_deleted_manifest(repo_root, stray_deleted_files)

    return ApplyResult(applied=applied, escalations=escalations)
