"""Namer: LLM-assisted semantic naming for placeholder pkg_NNN clusters.

This module is the LAST semantic step in the pipeline. It runs only after
structure is stable and validation has passed. It does not propose structural
moves — only names.

Algorithm
---------
1. gather_context: builds a text blob from cluster/plan summaries + optional
   graphify --wiki output + optional graphify explain snippets.
2. name_clusters: calls claude via messages.parse(output_format=RenameMap)
   with the context as a cached system message. Returns a typed RenameMap.
3. write_rename_map: serializes the RenameMap to JSON.

Cardinal rules (from plan v7):
- Never compute a score/threshold/weighted-sum. Structural signals come from
  graphify.analyze.* passthroughs already in the plan/view.
- Misplaced = binary (already resolved by applicator before namer runs).
- Mechanical first, semantic last: this module is the semantic step.
- LLM only names — no structural moves.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from refactor_plan.interface.cluster_view import GraphView
from refactor_plan.planning.planner import RefactorPlan


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public data models
# ---------------------------------------------------------------------------

_CONTEXT_CAP = 100_000  # max chars for the full context blob


class RenameEntry(BaseModel):
    old_name: str    # e.g. "pkg_001" or "pkg_001.SomeClass"
    new_name: str    # e.g. "ingest" or "ingest.DocumentLoader"
    rationale: str   # short human-readable justification


class RenameMap(BaseModel):
    entries: list[RenameEntry]


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def _cluster_summary(plan: RefactorPlan, view: GraphView) -> str:
    """Plain-text summary of clusters, files, cohesion, and splitting candidates."""
    lines: list[str] = ["## Cluster Summary\n"]
    for cluster in plan.clusters:
        lines.append(f"### {cluster.name} (community_id={cluster.community_id})")
        lines.append(f"Cohesion: {cluster.cohesion:.3f}")
        lines.append("Files:")
        for f in cluster.files:
            lines.append(f"  - {f}")

        # Splitting candidates that mention this cluster's files or pkg name
        relevant = [
            sc for sc in plan.splitting_candidates
            if cluster.name in sc.question or any(f in sc.question for f in cluster.files)
        ]
        if relevant:
            lines.append("Splitting candidates:")
            for sc in relevant:
                lines.append(f"  [{sc.type}] {sc.question}")
                lines.append(f"    why: {sc.why}")
        lines.append("")

    if plan.symbol_moves:
        lines.append("## Symbol Moves (approved)")
        for sm in plan.symbol_moves:
            if sm.approved:
                lines.append(
                    f"  {sm.label} from {sm.src_file} → {sm.dest_cluster}"
                )
        lines.append("")

    if view.god_nodes:
        lines.append("## God Nodes (top entries from graphify.analyze.god_nodes)")
        for gn in view.god_nodes[:5]:
            lines.append(f"  - {gn}")
        lines.append("")

    if view.surprising_connections:
        lines.append("## Surprising Connections (top entries)")
        for sc in view.surprising_connections[:5]:
            lines.append(f"  - {sc}")
        lines.append("")

    return "\n".join(lines)


def _run_wiki(repo_root: Path) -> str:
    """Run `graphify --wiki <repo_root>` and read per-community markdown articles.

    Returns the concatenated wiki content, or empty string on failure.
    """
    wiki_dir = repo_root / "graphify-out" / "wiki"
    try:
        result = subprocess.run(
            ["graphify", "--wiki", str(repo_root)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning(
                "graphify --wiki exited with code %d: %s",
                result.returncode,
                result.stderr[:500],
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("graphify --wiki failed (%s); continuing without wiki.", exc)

    # Read whichever .md articles exist in the wiki dir (may have been produced
    # by a prior run even if the subprocess above failed).
    if not wiki_dir.is_dir():
        return ""

    articles: list[str] = []
    for md_path in sorted(wiki_dir.glob("*.md")):
        try:
            articles.append(f"<!-- {md_path.name} -->\n{md_path.read_text()}")
        except OSError as exc:
            logger.warning("Could not read wiki article %s: %s", md_path, exc)

    if not articles:
        return ""

    return "\n\n".join(articles)


def _run_explain(label: str) -> str:
    """Run `graphify explain "<label>"` and return its stdout, or empty string."""
    try:
        result = subprocess.run(
            ["graphify", "explain", label],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "graphify explain %r exited %d: %s",
                label,
                result.returncode,
                result.stderr[:200],
            )
            return ""
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("graphify explain %r failed (%s).", label, exc)
        return ""


def _truncate_section(text: str, cap: int) -> str:
    """Truncate text to cap chars, appending a note if truncated."""
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n... [truncated at {cap} chars]"


def gather_context(
    plan: RefactorPlan,
    view: GraphView,
    repo_root: Path,
    _graph_json_path: Path,
    *,
    use_wiki: bool = True,
    use_explain: bool = True,
) -> str:
    """Build the context blob that becomes the LLM's system message.

    Concatenates:
      - cluster_summary (from plan + view)
      - optional `graphify --wiki` per-community markdown articles
      - per-cluster `graphify explain` snippets for up to the first 3 god_node labels
    Returns a single string capped at _CONTEXT_CAP chars.
    """
    # Section 1: static cluster summary (~no subprocess)
    summary = _cluster_summary(plan, view)

    # Section 2: graphify --wiki output (cached per community)
    wiki_text = ""
    if use_wiki:
        wiki_text = _run_wiki(repo_root)
        if wiki_text:
            wiki_text = f"\n\n## Graphify Wiki Articles\n\n{wiki_text}"

    # Section 3: per-cluster graphify explain snippets
    explain_text = ""
    if use_explain:
        explain_parts: list[str] = []
        # Collect up to 3 god_node labels total (ranked by graphify)
        god_labels = [
            gn.get("label", "") or gn.get("id", "")
            for gn in view.god_nodes[:3]
            if gn.get("label") or gn.get("id")
        ]
        for label in god_labels:
            snippet = _run_explain(str(label))
            if snippet:
                explain_parts.append(f"### explain: {label}\n{snippet}")
        if explain_parts:
            explain_text = "\n\n## Graphify Explain Snippets\n\n" + "\n\n".join(explain_parts)

    # Assemble and cap
    full = summary + wiki_text + explain_text
    return _truncate_section(full, _CONTEXT_CAP)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def name_clusters(
    plan: RefactorPlan,
    view: GraphView,
    repo_root: Path,
    graph_json_path: Path,
    *,
    model: str = "claude-opus-4-7",
    anthropic_client=None,
    use_wiki: bool = True,
    use_explain: bool = True,
) -> RenameMap:
    """Call Claude to produce a RenameMap for all pkg_NNN placeholders.

    Parameters
    ----------
    plan:
        The RefactorPlan produced by planner.plan().
    view:
        The GraphView produced by cluster_view.build_view().
    repo_root:
        Root of the target repository (for graphify subprocesses).
    graph_json_path:
        Path to graph.json (passed to gather_context for provenance; not
        directly read here beyond what the view already captured).
    model:
        Claude model identifier.
    anthropic_client:
        Optional pre-built Anthropic client (used in tests to inject a mock).
    use_wiki, use_explain:
        Whether to run the graphify --wiki / explain subprocesses.

    Returns
    -------
    RenameMap
        Typed rename map from the LLM.  Contains only name proposals — no
        structural changes.
    """
    import anthropic
    from anthropic.types import TextBlockParam

    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()

    context = gather_context(
        plan,
        view,
        repo_root,
        graph_json_path,
        use_wiki=use_wiki,
        use_explain=use_explain,
    )

    system: list[TextBlockParam] = [
        cast(
            TextBlockParam,
            {
                "type": "text",
                "text": context,
                "cache_control": {"type": "ephemeral"},
            },
        ),
    ]

    instruction = (
        "Propose a rename map for the placeholder pkg_NNN names and any split-out symbols. "
        "Return ONLY the rename map; do not propose structural moves. "
        "Names should be short, lowercase package names following Python conventions."
    )

    response = anthropic_client.messages.parse(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": instruction}],
        output_format=RenameMap,
    )
    # B3: guard against None parsed_output (SDK type includes None even in parse mode)
    if response.parsed_output is None:
        raise RuntimeError("Anthropic response had no parsed_output")
    return response.parsed_output


def write_rename_map(rmap: RenameMap, output_path: Path) -> None:
    """Serialize the RenameMap to JSON at output_path."""
    output_path.write_text(rmap.model_dump_json(indent=2))
