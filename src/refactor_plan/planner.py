"""Planner: turn a GraphView into a RefactorPlan and serialize it.

Algorithm overview
------------------
1. Allocate pkg_NNN names: sort file_clusters by len(files) descending,
   tie-break by lowest cluster id. Largest → pkg_001, next → pkg_002, etc.
2. File moves: dest = pkg_NNN/<basename>. Skip if src == dest (no-op).
3. Symbol moves: passthrough from view.misplaced_symbols, excluding methods
   (labels starting with "."). Each symbol gets a dest_file chosen by
   counting graph edges to nodes in the dest cluster's files.
4. Shim candidates: repo-introspection only (ast + tomllib), three triggers:
   A. file defines a top-level name appearing in any __init__.py's __all__.
   B. file is directly under <repo_root>/src/ or <repo_root>/ (top-level pkg).
   C. file appears in pyproject.toml [project.scripts] or [project.entry-points.*].
5. Splitting candidates: passthrough from view.suggested_questions, types
   "low_cohesion" and "bridge_node" only, sorted by (type, question).
6. All lists sorted by stable keys for determinism.

Shim matching rule (Trigger A)
-------------------------------
A file's top-level ClassDef/FunctionDef/AsyncFunctionDef/Assign names are
intersected with every entry in __all__ across all __init__.py files.
If the intersection is non-empty, trigger "in __all__" fires.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import networkx as nx

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-reuse-def]

from pydantic import BaseModel

from refactor_plan.cluster_view import GraphView, load_graph


class FileMove(BaseModel):
    src: str        # repo-relative source path
    dest: str       # pkg_NNN/<basename>
    cluster: str    # e.g. "pkg_001"
    cohesion: float # cluster cohesion (passthrough)


class SymbolMove(BaseModel):
    symbol_id: str
    label: str
    src_file: str
    dest_cluster: str    # pkg_NNN where target_community lives
    dest_file: str       # repo-relative path: dest_cluster/<basename>
    host_community: int
    target_community: int
    approved: bool = False  # human-gated


class BlockedMove(BaseModel):
    symbol_id: str
    label: str
    src_file: str
    target_community: int
    reason: str


class ShimCandidate(BaseModel):
    src: str                  # repo-relative original path
    triggers: list[str]       # which heuristic(s) fired


class SplittingCandidate(BaseModel):
    """Passthrough from suggest_questions[low_cohesion]/[bridge_node]."""
    type: str      # "low_cohesion" | "bridge_node"
    question: str
    why: str


class ClusterAlloc(BaseModel):
    name: str          # "pkg_001"
    community_id: int
    files: list[str]
    cohesion: float


class RefactorPlan(BaseModel):
    clusters: list[ClusterAlloc]
    file_moves: list[FileMove]
    symbol_moves: list[SymbolMove]
    shim_candidates: list[ShimCandidate]
    splitting_candidates: list[SplittingCandidate]
    blocked_moves: list[BlockedMove] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _allocate_pkg_names(view: GraphView) -> dict[int, str]:
    """Sort clusters by (size desc, community_id asc) → pkg_001, pkg_002, …"""
    sorted_clusters = sorted(
        view.file_clusters,
        key=lambda fc: (-len(fc.files), fc.id),
    )
    return {fc.id: f"pkg_{rank:03d}" for rank, fc in enumerate(sorted_clusters, start=1)}


def _parse_all_from_init(init_path: Path) -> set[str]:
    """Return the set of names in __all__ from an __init__.py, or empty set."""
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, (list, tuple)):
                            return {str(x) for x in value}
                    except (ValueError, TypeError):
                        pass
    return set()


def _collect_all_exports(repo_root: Path) -> set[str]:
    """Collect all names from every top-level __init__.py under repo_root."""
    exports: set[str] = set()
    for init_path in repo_root.rglob("__init__.py"):
        exports |= _parse_all_from_init(init_path)
    return exports


# Cache of top-level names per absolute file path (populated lazily).
_top_level_names_cache: dict[Path, set[str]] = {}


def _top_level_names(src_path: Path) -> set[str]:
    """Return names of top-level ClassDef, FunctionDef, AsyncFunctionDef, and
    simple Assign targets (ast.Name only) in src_path.

    Results are cached by absolute path to avoid re-parsing for every cluster.
    """
    abs_path = src_path.resolve()
    if abs_path in _top_level_names_cache:
        return _top_level_names_cache[abs_path]
    try:
        tree = ast.parse(abs_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        _top_level_names_cache[abs_path] = set()
        return set()
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    _top_level_names_cache[abs_path] = names
    return names


def _parse_pyproject_scripts(repo_root: Path) -> set[str]:
    """Return file paths (values) from pyproject.toml [project.scripts] and
    [project.entry-points.*]. Entry-point values are of the form
    'pkg.module:callable'; we extract the module path portion."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return set()
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return set()
    refs: set[str] = set()
    project = data.get("project", {})
    # [project.scripts] values like "module.sub:func"
    for val in project.get("scripts", {}).values():
        refs.add(val)
    # [project.entry-points.*]
    for group in project.get("entry-points", {}).values():
        for val in group.values():
            refs.add(val)
    return refs


def _shim_triggers(src: str, repo_root: Path, all_exports: set[str], pyproject_refs: set[str], source_map: dict[str, Path] | None = None) -> list[str]:
    """Return list of trigger names that fire for this source file."""
    triggers: list[str] = []
    p = Path(src)

    # Trigger A: file defines a top-level name that appears in any __all__.
    # We compare top-level ClassDef/FunctionDef/AsyncFunctionDef/Assign names
    # against the collected __all__ exports (exact match, case-sensitive).
    # Path resolution: prefer source_map (authoritative, CWD-independent);
    # fall back to candidate probing when source_map is None.
    src_path: Path | None = None
    if source_map is not None and src in source_map:
        src_path = source_map[src]
    else:
        for candidate in [repo_root / src, Path(src)]:
            if candidate.exists():
                src_path = candidate
                break
    if src_path is not None and bool(_top_level_names(src_path) & all_exports):
        triggers.append("in __all__")

    # Trigger B: file is directly under <repo_root>/src/ or <repo_root>/
    try:
        full = (repo_root / src).resolve()
        top_src = (repo_root / "src").resolve()
        top_root = repo_root.resolve()
        if full.parent == top_src or full.parent == top_root:
            triggers.append("top_level")
    except Exception:
        pass

    # Trigger C: file appears in pyproject.toml scripts/entry-points values
    # Values are dotted module paths like "refactor_plan.cli:app"
    # Check if the dotted module path corresponds to this file.
    src_no_py = str(p.with_suffix("")).replace("/", ".").replace("\\", ".")
    for ref in pyproject_refs:
        # strip the ":callable" suffix if present
        module_part = ref.split(":")[0].strip()
        if module_part == src_no_py or src_no_py.endswith(module_part):
            triggers.append("pyproject_scripts")
            break

    return triggers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _dest_file_for_symbol(
    symbol_id: str,
    host_file: str,
    target_community: int,
    dest_cluster: str,
    view: GraphView,
    G: nx.Graph,
    repo_root: Path,
    file_to_mod: dict[str, str],
) -> str:
    """Choose the destination file for a misplaced symbol by counting graph edges.

    Algorithm (deterministic):
    1. Gather candidate files from the target community's FileCluster.
    2. Filter: skip the symbol's host file; keep only files that exist on disk.
    3. For each candidate, count edges in G between symbol_id and any node
       whose source_file matches that candidate (rationale nodes excluded).
    4. Pick the file with the highest count.
       Ties broken by lowest Path(f).name lexicographically.
    5. Fallback 1: if no candidate has >=1 edge, pick the candidate whose
       basename matches the host file's stem (e.g. god.py → god.py in dest).
    6. Fallback 2: if still no match, pick the lexicographically first candidate.
    7. Returns dest_cluster/mod_MMM.py (placeholder name via file_to_mod).
    """
    target_fc = next((fc for fc in view.file_clusters if fc.id == target_community), None)
    if target_fc is None:
        # Degenerate: no cluster info; fall back to host file's mod name if available
        mod_name = file_to_mod.get(host_file, f"mod_001.py")
        return f"{dest_cluster}/{mod_name}"

    def _file_exists_on_disk(f: str) -> bool:
        """Check if a source file from the graph exists on disk (handles graphify paths)."""
        for candidate in [repo_root / f, Path(f)]:
            if candidate.exists():
                return True
        return False

    # Gather candidates: existing files in target cluster, excluding host file
    candidates = [
        f for f in target_fc.files
        if f != host_file and _file_exists_on_disk(f)
    ]
    # If no candidates remain (e.g. all filtered out), include them anyway
    if not candidates:
        candidates = [f for f in target_fc.files if f != host_file]
    if not candidates:
        # Only file in cluster is the host — use the host's mod name in dest
        mod_name = file_to_mod.get(host_file, "mod_001.py")
        return f"{dest_cluster}/{mod_name}"

    # Count edges from symbol_id to nodes in each candidate file
    neighbors = list(G.neighbors(symbol_id)) if symbol_id in G else []
    file_counts: dict[str, int] = {f: 0 for f in candidates}
    for nb in neighbors:
        if "rationale" in nb:
            continue
        nb_source = G.nodes[nb].get("source_file", "")
        if nb_source in file_counts:
            file_counts[nb_source] += 1

    max_count = max(file_counts.values())
    if max_count >= 1:
        # Pick highest-count file; tie-break by lexicographic basename
        chosen = min(
            (f for f, c in file_counts.items() if c == max_count),
            key=lambda f: Path(f).name,
        )
    else:
        # Fallback 1: basename matches host file's stem
        host_stem = Path(host_file).stem
        fallback1 = next(
            (f for f in candidates if Path(f).stem == host_stem), None
        )
        if fallback1 is not None:
            chosen = fallback1
        else:
            # Fallback 2: lexicographically first candidate
            chosen = min(candidates, key=lambda f: Path(f).name)

    # Route chosen source path through file_to_mod to get the placeholder name.
    # file_to_mod is built from all clusters, so chosen is guaranteed to be keyed.
    assert chosen in file_to_mod, (
        f"Chosen file {chosen!r} not found in file_to_mod; "
        "this indicates a cluster data inconsistency"
    )
    return f"{dest_cluster}/{file_to_mod[chosen]}"


def plan(view: GraphView, repo_root: Path, graph_json_path: Path | None = None, *, source_map: dict[str, Path] | None = None) -> RefactorPlan:
    """Turn a GraphView into a RefactorPlan (deterministic, no new analysis).

    graph_json_path is required for A3 (dest_file computation). When None,
    symbol dest_file falls back to dest_cluster/<host_basename>.
    """

    # 1. Allocate pkg_NNN names.
    community_to_pkg = _allocate_pkg_names(view)

    # Build sorted ClusterAlloc list (same order as community_to_pkg allocation).
    sorted_clusters = sorted(
        view.file_clusters,
        key=lambda fc: (-len(fc.files), fc.id),
    )
    clusters = [
        ClusterAlloc(
            name=community_to_pkg[fc.id],
            community_id=fc.id,
            files=sorted(fc.files),
            cohesion=fc.cohesion,
        )
        for fc in sorted_clusters
    ]

    # 1b. Allocate per-cluster file mapping: original_path → mod_MMM.py basename.
    # Within each cluster, files are sorted ascending by path (same key used
    # everywhere) so the assignment is deterministic.
    file_to_mod: dict[str, str] = {}
    for cluster in clusters:
        for mmm, src in enumerate(sorted(cluster.files), start=1):
            file_to_mod[src] = f"mod_{mmm:03d}.py"

    # 2. File moves (sorted by src for determinism).
    file_moves: list[FileMove] = []
    for fc in sorted_clusters:
        pkg_name = community_to_pkg[fc.id]
        for src in sorted(fc.files):
            dest = f"{pkg_name}/{file_to_mod[src]}"
            if src == dest:
                continue
            file_moves.append(
                FileMove(
                    src=src,
                    dest=dest,
                    cluster=pkg_name,
                    cohesion=fc.cohesion,
                )
            )
    # Sort globally by (cluster, src) for determinism.
    file_moves.sort(key=lambda m: (m.cluster, m.src))

    # Load graph for dest_file computation (A3).
    G: nx.Graph | None = None
    if graph_json_path is not None:
        try:
            G = load_graph(graph_json_path)
        except Exception:
            G = None

    # 3. Symbol moves: skip methods (A1). Compute dest_file (A3).
    symbol_moves: list[SymbolMove] = []
    blocked_moves: list[BlockedMove] = []
    for m in view.misplaced_symbols:
        # A1: skip bound methods — rope's create_move operates on top-level
        # definitions only; passing methods causes "Destination attribute not found".
        if m.label.startswith("."):
            continue
        if m.target_community not in community_to_pkg:
            blocked_moves.append(
                BlockedMove(
                    symbol_id=m.symbol_id,
                    label=m.label,
                    src_file=m.host_file,
                    target_community=m.target_community,
                    reason=(
                        f"target community {m.target_community} has no file-backed cluster; "
                        f"available: {sorted(community_to_pkg.keys())}"
                    ),
                )
            )
            continue
        dest_cluster = community_to_pkg[m.target_community]
        # A3: pick per-symbol dest file by counting graph edges
        if G is not None:
            dest_file = _dest_file_for_symbol(
                m.symbol_id,
                m.host_file,
                m.target_community,
                dest_cluster,
                view,
                G,
                repo_root,
                file_to_mod,
            )
        else:
            dest_file = f"{dest_cluster}/{file_to_mod.get(m.host_file, 'mod_001.py')}"
        symbol_moves.append(
            SymbolMove(
                symbol_id=m.symbol_id,
                label=m.label,
                src_file=m.host_file,
                dest_cluster=dest_cluster,
                dest_file=dest_file,
                host_community=m.host_community,
                target_community=m.target_community,
                approved=False,
            )
        )
    # misplaced_symbols already sorted by (host_file, label) in cluster_view.
    symbol_moves.sort(key=lambda s: (s.src_file, s.label))

    # 4. Shim candidates (repo-introspection only).
    all_exports = _collect_all_exports(repo_root)
    pyproject_refs = _parse_pyproject_scripts(repo_root)

    shim_map: dict[str, list[str]] = {}  # src → triggers
    for fc in view.file_clusters:
        for src in fc.files:
            triggers = _shim_triggers(src, repo_root, all_exports, pyproject_refs, source_map=source_map)
            if triggers:
                if src not in shim_map:
                    shim_map[src] = triggers
                else:
                    # merge triggers (dedup)
                    existing = set(shim_map[src])
                    for t in triggers:
                        if t not in existing:
                            shim_map[src].append(t)

    shim_candidates = [
        ShimCandidate(src=src, triggers=sorted(triggers))
        for src, triggers in sorted(shim_map.items())
    ]

    # 5. Splitting candidates (passthrough, sorted by (type, question)).
    splitting_candidates = sorted(
        [
            SplittingCandidate(
                type=q["type"],
                question=q["question"],
                why=q["why"],
            )
            for q in view.suggested_questions
            if q.get("type") in ("low_cohesion", "bridge_node")
        ],
        key=lambda c: (c.type, c.question),
    )

    return RefactorPlan(
        clusters=clusters,
        file_moves=file_moves,
        symbol_moves=symbol_moves,
        shim_candidates=shim_candidates,
        splitting_candidates=splitting_candidates,
        blocked_moves=blocked_moves,
    )


def write_plan(refactor_plan: RefactorPlan, output_path: Path) -> None:
    """Serialize RefactorPlan to JSON at output_path."""
    output_path.write_text(refactor_plan.model_dump_json(indent=2))
