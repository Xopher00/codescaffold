"""Run lint-imports and return a typed ContractValidationResult."""

from __future__ import annotations

import io
import os
import re
from contextlib import redirect_stdout
from pathlib import Path

from .models import ContractValidationResult

_CONFIG_FILENAME = ".importlinter"
_CHECKED_RE = re.compile(r"(\d+)\s+contract[s]?\s+checked")
_FAILED_RE = re.compile(r"(\d+)\s+contract[s]?\s+broken")


def run_lint_imports(repo_path: Path) -> ContractValidationResult:
    """Run lint-imports against repo_path/.importlinter.

    Returns a result with succeeded=True and contracts_checked=0 if no
    .importlinter file exists (non-intrusive opt-in).
    """
    repo_path = Path(repo_path).resolve()
    config = repo_path / _CONFIG_FILENAME

    if not config.exists():
        return ContractValidationResult(
            succeeded=True,
            raw_output="(no .importlinter)",
            contracts_checked=0,
            contracts_failed=0,
        )

    try:
        from importlinter.cli import lint_imports
    except ImportError:
        return ContractValidationResult(
            succeeded=False,
            raw_output="import-linter is not installed",
            contracts_checked=0,
            contracts_failed=1,
        )

    buf = io.StringIO()
    cwd = os.getcwd()
    os.chdir(repo_path)
    try:
        with redirect_stdout(buf):
            try:
                exit_code = lint_imports(config_filename=str(config), no_cache=True)
            except SystemExit as e:
                exit_code = int(e.code) if e.code is not None else 1
    finally:
        os.chdir(cwd)

    output = buf.getvalue()
    return ContractValidationResult(
        succeeded=(exit_code == 0),
        raw_output=output,
        contracts_checked=_parse_count(output, _CHECKED_RE),
        contracts_failed=_parse_count(output, _FAILED_RE),
    )


def _parse_count(text: str, pattern: re.Pattern) -> int:
    m = pattern.search(text)
    return int(m.group(1)) if m else 0
