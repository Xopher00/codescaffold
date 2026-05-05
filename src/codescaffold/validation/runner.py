"""Run compileall + pytest (+ optional lint-imports) in a repo directory."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codescaffold.contracts.models import ViolationReport


@dataclass(frozen=True)
class ValidationResult:
    compileall_ok: bool
    pytest_ok: bool
    pytest_summary: str
    failed_steps: tuple[str, ...]
    contracts_ok: bool = True
    contract_violation: "ViolationReport | None" = None

    @property
    def succeeded(self) -> bool:
        return self.compileall_ok and self.pytest_ok and self.contracts_ok


def run_validation(repo: Path) -> ValidationResult:
    """Run compileall and pytest inside repo, return a typed result.

    pytest is skipped (treated as passing) if no tests/ directory exists.
    """
    repo = Path(repo).resolve()
    failed: list[str] = []

    # --- compileall ---
    compile_result = subprocess.run(
        ["python", "-m", "compileall", "-q", "src"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    compileall_ok = compile_result.returncode == 0
    if not compileall_ok:
        failed.append("compileall")

    # --- pytest ---
    tests_dir = repo / "tests"
    if not tests_dir.exists():
        pytest_ok = True
        pytest_summary = "no tests/ directory — skipped"
    else:
        pytest_result = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-q"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        pytest_ok = pytest_result.returncode == 0
        pytest_summary = (pytest_result.stdout + pytest_result.stderr).strip()
        if not pytest_ok:
            failed.append("pytest")

    # --- import-linter contracts (opt-in: only if .importlinter exists) ---
    contracts_ok = True
    if (repo / ".importlinter").exists():
        from codescaffold.contracts.validator import run_lint_imports
        cr = run_lint_imports(repo)
        contracts_ok = cr.succeeded
        if not contracts_ok:
            failed.append("contracts")

    return ValidationResult(
        compileall_ok=compileall_ok,
        pytest_ok=pytest_ok,
        pytest_summary=pytest_summary,
        failed_steps=tuple(failed),
        contracts_ok=contracts_ok,
    )
