from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

ValidationMode = Literal["structural", "behavioral", "all"]


class CommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str


class ValidationReport(BaseModel):
    passed: bool
    commands: list[CommandResult] = []
    rolled_back: bool = False


def _pytest_available(repo_root: Path) -> bool:
    """Fallback test-presence check when no ProjectLayout is provided."""
    if shutil.which("pytest") is None:
        return False
    for pattern in ("test_*.py", "*_test.py"):
        if any(repo_root.rglob(pattern)):
            return True
    return False


def _importability_check(source_root: Path, root_package: str) -> CommandResult:
    """Run a quick import smoke test for root_package with source_root on PYTHONPATH.

    This is the worktree-safe substitute for 'pip install -e . --no-deps':
    it verifies the package is importable from the moved source tree without
    mutating the editable-install registration of the original repo.
    """
    import os
    env = {**os.environ, "PYTHONPATH": str(source_root)}
    proc = subprocess.run(
        [sys.executable, "-c", f"import {root_package}"],
        capture_output=True,
        text=True,
        env=env,
    )
    return CommandResult(
        command=f"python -c 'import {root_package}'",
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def validate(
    repo_root: Path,
    commands: list[str] | None = None,
    env: dict[str, str] | None = None,
    mode: ValidationMode = "all",
    layout=None,  # ProjectLayout | None — avoid circular import at module level
) -> ValidationReport:
    """Run validation commands against repo_root.

    mode="structural"  — compileall only; does not require pytest.
    mode="behavioral"  — importability smoke test + pytest; skipped if no tests.
    mode="all"         — structural first, then behavioral if tests exist.

    Explicit commands override mode when provided.
    layout (ProjectLayout) — when supplied, enables targeted compileall and
    test-presence detection from config.  Falls back to heuristics when None.
    """
    import os
    run_env = {**os.environ, **(env or {})}

    if commands is not None:
        return _run_commands(commands, repo_root, run_env)

    # Determine compile target
    compile_target = "."
    if layout is not None:
        try:
            compile_target = str(layout.source_root.relative_to(repo_root))
        except ValueError:
            compile_target = str(layout.source_root)

    structural_cmds = [f"python -m compileall {compile_target}"]

    # Determine whether tests exist
    if layout is not None:
        has_tests = layout.has_tests
        source_root = layout.source_root
        root_package = layout.root_package
    else:
        has_tests = _pytest_available(repo_root)
        source_root = repo_root / "src" if (repo_root / "src").exists() else repo_root
        root_package = ""

    if mode == "structural":
        return _run_commands(structural_cmds, repo_root, run_env)

    if mode == "behavioral":
        if not has_tests:
            logger.debug("No tests discovered; skipping behavioral validation.")
            return ValidationReport(passed=True, commands=[])
        report = ValidationReport(passed=True, commands=[])
        if root_package:
            imp_result = _importability_check(source_root, root_package)
            report.commands.append(imp_result)
            if imp_result.exit_code != 0:
                report.passed = False
                return report
        pytest_report = _run_commands(["pytest -q"], repo_root, run_env)
        report.commands.extend(pytest_report.commands)
        if not pytest_report.passed:
            report.passed = False
        return report

    # mode == "all"
    report = _run_commands(structural_cmds, repo_root, run_env)
    if not report.passed:
        return report

    if not has_tests:
        logger.debug("No tests discovered; skipping pytest in 'all' mode.")
        return report

    if root_package:
        imp_result = _importability_check(source_root, root_package)
        report.commands.append(imp_result)
        if imp_result.exit_code != 0:
            report.passed = False
            return report

    pytest_report = _run_commands(["pytest -q"], repo_root, run_env)
    report.commands.extend(pytest_report.commands)
    if not pytest_report.passed:
        report.passed = False
    return report


def _run_commands(
    cmds: list[str],
    repo_root: Path,
    run_env: dict[str, str],
) -> ValidationReport:
    results: list[CommandResult] = []
    all_passed = True
    for cmd in cmds:
        proc = subprocess.run(
            shlex.split(cmd),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            env=run_env,
        )
        results.append(CommandResult(
            command=cmd,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        ))
        if proc.returncode != 0:
            all_passed = False
    return ValidationReport(passed=all_passed, commands=results)
