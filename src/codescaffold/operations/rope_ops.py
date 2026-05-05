"""Typed wrappers over rope_mcp_server.refactoring.

Every function in that module returns a JSON string. This module parses those
strings, raises RopeOperationError on failure, and returns typed dataclasses.
"""

from __future__ import annotations

import json

from rope_mcp_server import refactoring as _rope

from .errors import RopeArgumentError, RopeRefactoringError, RopeUnexpectedError
from .results import RopeChangeResult, SymbolInfo


def _unwrap(op: str, raw: str, args: dict) -> dict:
    data = json.loads(raw)
    if data.get("success"):
        return data
    err = data.get("error", "unknown error")
    if "Refactoring error:" in err:
        raise RopeRefactoringError(op, err, args)
    if "Unexpected error:" in err:
        raise RopeUnexpectedError(op, err, args)
    raise RopeArgumentError(op, err, args)


def move_symbol(
    project_path: str,
    source_file: str,
    symbol_name: str,
    dest_file: str,
) -> RopeChangeResult:
    args = dict(
        project_path=project_path,
        source_file=source_file,
        symbol_name=symbol_name,
        dest_file=dest_file,
    )
    data = _unwrap("move_symbol", _rope.move_symbol(**args), args)
    return RopeChangeResult(changed_files=tuple(data["changed_files"]))


def rename_symbol(
    project_path: str,
    file_path: str,
    symbol_name: str,
    new_name: str,
) -> RopeChangeResult:
    args = dict(
        project_path=project_path,
        file_path=file_path,
        symbol_name=symbol_name,
        new_name=new_name,
    )
    data = _unwrap("rename_symbol", _rope.rename_symbol(**args), args)
    return RopeChangeResult(changed_files=tuple(data["changed_files"]))


def move_module(
    project_path: str,
    module_path: str,
    dest_folder: str,
) -> RopeChangeResult:
    args = dict(
        project_path=project_path,
        module_path=module_path,
        dest_folder=dest_folder,
    )
    data = _unwrap("move_module", _rope.move_module(**args), args)
    return RopeChangeResult(changed_files=tuple(data["changed_files"]))


def list_symbols(project_path: str, file_path: str) -> list[SymbolInfo]:
    args = dict(project_path=project_path, file_path=file_path)
    data = _unwrap("list_symbols", _rope.list_symbols(**args), args)
    return [
        SymbolInfo(
            name=s["name"],
            type=s["type"],
            line=s["line"],
            col_offset=s["col_offset"],
            byte_offset=s["byte_offset"],
        )
        for s in data["symbols"]
    ]


def close_rope_project(project_path: str) -> None:
    args = dict(project_path=project_path)
    _unwrap("close_rope_project", _rope.close_rope_project(**args), args)
