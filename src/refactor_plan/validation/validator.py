from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel


class CommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str


class ValidationReport(BaseModel):
    passed: bool
    commands: list[CommandResult] = []
    rolled_back: bool = False


_DEFAULT_COMMANDS = [
    "python -m compileall .",
    "pytest -q",
]


def validate(
    repo_root: Path,
    commands: list[str] | None = None,
) -> ValidationReport:
    cmds = commands if commands is not None else _DEFAULT_COMMANDS
    results: list[CommandResult] = []

    all_passed = True
    for cmd in cmds:
        proc = subprocess.run(
            shlex.split(cmd),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
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
