from __future__ import annotations

import ast
from pathlib import Path

import libcst as cst
import networkx as nx

from refactor_plan.interface.cluster_view import ClusterView

_MAX_RELATED = 5


# ---------------------------------------------------------------------------
# Graph context for a single symbol
# ---------------------------------------------------------------------------

def _build_symbol_context(
    symbol_name: str,
    file_path: Path,
    G: nx.Graph,
) -> dict[str, list[str]]:
    """Walk the graph to find what a symbol calls, is called by, and uses."""
    target_id: str | None = None
    for nid, attrs in G.nodes(data=True):
        if attrs.get("source_file") != str(file_path):
            continue
        label: str = attrs.get("label", "")
        if label == symbol_name or label == f"{symbol_name}()":
            target_id = nid
            break

    if target_id is None:
        return {"methods": [], "calls": [], "called_by": [], "uses_types": []}

    methods: list[str] = []
    calls_out: list[str] = []
    called_by: list[str] = []
    uses_types: list[str] = []

    for src, dst, attrs in G.edges(data=True):
        if src != target_id and dst != target_id:
            continue

        relation = attrs.get("relation", "")
        orig_src = attrs.get("_src", src)
        orig_tgt = attrs.get("_tgt", dst)
        neighbor = dst if src == target_id else src

        if relation == "method" and orig_src == target_id:
            label = G.nodes[neighbor].get("label", "")
            if label:
                methods.append(label)
        elif relation == "calls":
            if orig_src == target_id:
                label = G.nodes.get(orig_tgt, {}).get("label", "")
                if label:
                    calls_out.append(label)
            elif orig_tgt == target_id:
                label = G.nodes.get(orig_src, {}).get("label", "")
                if label:
                    called_by.append(label)
        elif relation == "uses" and orig_src == target_id:
            label = G.nodes.get(orig_tgt, {}).get("label", "")
            if label and not label.endswith(".py"):
                uses_types.append(label)

    return {
        "methods": sorted(set(methods))[:_MAX_RELATED],
        "calls": sorted(set(calls_out))[:_MAX_RELATED],
        "called_by": sorted(set(called_by))[:_MAX_RELATED],
        "uses_types": sorted(set(uses_types))[:_MAX_RELATED],
    }


# ---------------------------------------------------------------------------
# Source introspection
# ---------------------------------------------------------------------------

def _get_symbol_type(source: str, symbol_name: str) -> str | None:
    """Return 'class', 'function', or None if not found."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == symbol_name:
            return "class"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol_name:
            return "function"
    return None


def _is_docstring_stmt(stmt: object) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    if len(stmt.body) != 1 or not isinstance(stmt.body[0], cst.Expr):
        return False
    return isinstance(stmt.body[0].value, (cst.SimpleString, cst.ConcatenatedString))


def _extract_existing_docstring(source: str, symbol_name: str) -> str | None:
    """Return the current docstring text for a top-level symbol, or None."""
    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return None
    for stmt in tree.body:
        if not isinstance(stmt, (cst.FunctionDef, cst.ClassDef)):
            continue
        if stmt.name.value != symbol_name:
            continue
        body_stmts = list(stmt.body.body)
        if not body_stmts:
            continue
        first = body_stmts[0]
        if not isinstance(first, cst.SimpleStatementLine):
            continue
        if not _is_docstring_stmt(first):
            continue
        expr = first.body[0]
        if isinstance(expr, cst.Expr) and isinstance(expr.value, cst.SimpleString):
            raw = expr.value.value
            for q in ('"""', "'''", '"', "'"):
                if raw.startswith(q) and raw.endswith(q):
                    return raw[len(q):-len(q)].strip()
    return None


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_prompt(
    symbol_name: str,
    symbol_type: str,
    file_name: str,
    ctx: dict[str, list[str]],
    existing: str | None,
) -> str:
    lines = [
        f"Write a concise Python docstring for a {symbol_type} named `{symbol_name}` in `{file_name}`.",
        "",
        "Graph context:",
    ]
    if ctx["methods"]:
        lines.append(f"  Methods: {', '.join(ctx['methods'])}")
    if ctx["calls"]:
        lines.append(f"  Calls: {', '.join(ctx['calls'])}")
    if ctx["called_by"]:
        lines.append(f"  Called by: {', '.join(ctx['called_by'])}")
    if ctx["uses_types"]:
        lines.append(f"  Uses: {', '.join(ctx['uses_types'])}")
    if not any(ctx.values()):
        lines.append("  (no graph data available)")

    if existing:
        lines += [
            "",
            f"Existing docstring: {existing!r}",
            "",
            "Improve it if you can, or return it unchanged if it is already accurate.",
        ]

    lines += [
        "",
        "Rules:",
        "- One sentence only (the summary line)",
        "- No parameter, return, or raises documentation",
        "- Return ONLY the plain text — no quotes, no markdown, no explanation",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LibCST insertion
# ---------------------------------------------------------------------------

def _make_docstring_stmt(text: str) -> cst.SimpleStatementLine:
    safe = text.replace('"""', "'''")
    return cst.SimpleStatementLine(
        body=[cst.Expr(value=cst.SimpleString(f'"""{safe}"""'))],
        leading_lines=[],
    )


def _patch_body(
    stmts: list[cst.BaseStatement | cst.BaseSmallStatement],
    docstring_text: str,
) -> list[cst.BaseStatement | cst.BaseSmallStatement]:
    doc_stmt = _make_docstring_stmt(docstring_text)
    if stmts and _is_docstring_stmt(stmts[0]):
        return [doc_stmt] + list(stmts[1:])
    return [doc_stmt] + list(stmts)


class _DocstringInserter(cst.CSTTransformer):
    def __init__(self, symbol_name: str, docstring_text: str) -> None:
        self.symbol_name = symbol_name
        self.docstring_text = docstring_text
        self._depth = 0
        self.applied = False

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._depth += 1
        return True

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._depth += 1
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self._depth -= 1
        if self._depth == 0 and updated_node.name.value == self.symbol_name and not self.applied:
            self.applied = True
            new_stmts = _patch_body(list(updated_node.body.body), self.docstring_text)
            return updated_node.with_changes(
                body=updated_node.body.with_changes(body=new_stmts)
            )
        return updated_node

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._depth -= 1
        if self._depth == 0 and updated_node.name.value == self.symbol_name and not self.applied:
            self.applied = True
            new_stmts = _patch_body(list(updated_node.body.body), self.docstring_text)
            return updated_node.with_changes(
                body=updated_node.body.with_changes(body=new_stmts)
            )
        return updated_node


def _insert_or_replace_docstring(source: str, symbol_name: str, docstring_text: str) -> str:
    tree = cst.parse_module(source)
    return tree.visit(_DocstringInserter(symbol_name, docstring_text)).code


# ---------------------------------------------------------------------------
# Public API — pure helpers (no API calls)
# ---------------------------------------------------------------------------

def build_docstring_context(
    file_path: Path,
    symbol_name: str,
    view: ClusterView,
) -> str | None:
    """Return formatted graph context for a symbol — no API call.

    Returns None if the symbol is not found in the file.
    Pass the returned string to an LLM and ask it to write a one-sentence
    docstring, then call insert_docstring_text() with the result.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError:
        return None

    symbol_type = _get_symbol_type(source, symbol_name)
    if symbol_type is None:
        return None

    ctx = _build_symbol_context(symbol_name, file_path, view.G)
    existing = _extract_existing_docstring(source, symbol_name)
    return _build_prompt(symbol_name, symbol_type, file_path.name, ctx, existing)


def insert_docstring_text(
    file_path: Path,
    symbol_name: str,
    docstring_text: str,
) -> str | None:
    """Insert or replace the docstring for symbol_name in file_path.

    Returns an error message on failure, or None on success.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Cannot read {file_path}: {exc}"

    if _get_symbol_type(source, symbol_name) is None:
        return f"Symbol '{symbol_name}' not found in {file_path.name}"

    try:
        modified = _insert_or_replace_docstring(source, symbol_name, docstring_text)
    except cst.ParserSyntaxError as exc:
        return f"LibCST parse error: {exc}"

    try:
        file_path.write_text(modified, encoding="utf-8")
    except OSError as exc:
        return f"Cannot write {file_path}: {exc}"

    return None

