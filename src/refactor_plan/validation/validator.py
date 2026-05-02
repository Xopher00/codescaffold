from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel

from refactor_plan.applicator.rollback import rollback


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
    out_dir: Path,
    commands: list[str] | None = None,
) -> ValidationReport:
    cmds = commands if commands is not None else _DEFAULT_COMMANDS
    results: list[CommandResult] = []

    for cmd in cmds:
        proc = subprocess.run(
            cmd.split(),
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
            rollback(repo_root, out_dir)
            return ValidationReport(passed=False, commands=results, rolled_back=True)

    return ValidationReport(passed=True, commands=results)
