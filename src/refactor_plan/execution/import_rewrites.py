from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor, RemoveImportsVisitor


class MoveRecord(NamedTuple):
    old_module: str
    new_module: str
    symbols: list[str]  # empty = whole module moved


def _dotted(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""


class _CrossClusterImportRewriter(cst.CSTTransformer):
    def __init__(self, context: CodemodContext, moves: list[MoveRecord]) -> None:
        super().__init__()
        self._context = context
        # (old_module, symbol_or_empty) -> new_module
        self._map: dict[tuple[str, str], str] = {}
        for rec in moves:
            if rec.symbols:
                for sym in rec.symbols:
                    self._map[(rec.old_module, sym)] = rec.new_module
            else:
                self._map[(rec.old_module, "")] = rec.new_module

    def leave_ImportFrom(
        self,
        original_node: cst.ImportFrom,
        updated_node: cst.ImportFrom,
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel:
        if updated_node.module is None:
            return updated_node

        old_mod = _dotted(updated_node.module)

        # Star import: can't safely rewrite; leave untouched
        if isinstance(updated_node.names, cst.ImportStar):
            return updated_node

        if not isinstance(updated_node.names, (list, tuple)):
            return updated_node

        # Whole-module move: remap every named alias
        if (old_mod, "") in self._map:
            new_mod = self._map[(old_mod, "")]
            for alias in updated_node.names:
                sym = alias.name.value if isinstance(alias.name, cst.Name) else _dotted(alias.name)
                asname = None
                if alias.asname is not None and isinstance(alias.asname, cst.AsName):
                    inner = alias.asname.name
                    asname = inner.value if isinstance(inner, cst.Name) else None
                AddImportsVisitor.add_needed_import(self._context, new_mod, sym, asname)
            return cst.RemoveFromParent()

        # Per-symbol moves
        staying = []
        for alias in updated_node.names:
            sym = alias.name.value if isinstance(alias.name, cst.Name) else _dotted(alias.name)
            new_mod = self._map.get((old_mod, sym))
            if new_mod:
                asname = None
                if alias.asname is not None and isinstance(alias.asname, cst.AsName):
                    inner = alias.asname.name
                    asname = inner.value if isinstance(inner, cst.Name) else None
                AddImportsVisitor.add_needed_import(self._context, new_mod, sym, asname)
            else:
                staying.append(alias)

        if not staying:
            return cst.RemoveFromParent()
        if len(staying) < len(updated_node.names):
            cleaned = [
                a.with_changes(comma=cst.MaybeSentinel.DEFAULT) if i == len(staying) - 1 else a
                for i, a in enumerate(staying)
            ]
            return updated_node.with_changes(names=cleaned)
        return updated_node


def add_back_import(target_file: Path, symbol: str, new_module: str) -> bool:
    """Add `from new_module import symbol` to target_file if symbol still appears there.

    Used after a symbol move to keep the source file compilable when it still
    references the moved symbol in type annotations or class bodies.
    """
    source = target_file.read_text(encoding="utf-8")
    if symbol not in source:
        return False
    context = CodemodContext()
    tree = cst.parse_module(source)
    AddImportsVisitor.add_needed_import(context, new_module, symbol)
    tree = AddImportsVisitor(context).transform_module(tree)
    result = tree.code
    if result == source:
        return False
    target_file.write_text(result, encoding="utf-8")
    return True


def rewrite_cross_cluster_imports(target_file: Path, moves: list[MoveRecord]) -> bool:
    source = target_file.read_text(encoding="utf-8")
    context = CodemodContext()

    tree = cst.parse_module(source)
    rewriter = _CrossClusterImportRewriter(context, moves)
    tree = tree.visit(rewriter)

    # Apply queued add/remove passes (transform_module handles MetadataWrapper)
    tree = AddImportsVisitor(context).transform_module(tree)
    tree = RemoveImportsVisitor(context).transform_module(tree)

    result = tree.code
    if result == source:
        return False
    target_file.write_text(result, encoding="utf-8")
    return True
