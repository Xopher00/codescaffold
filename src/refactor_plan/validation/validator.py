"""validator: run validation commands after a refactor batch and rollback on failure."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

from pydantic import BaseModel

from refactor_plan.applicator.rope_runner import Escalation, rollback


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class ValidationCommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class ValidationReport(BaseModel):
    passed: bool
    commands: list[ValidationCommandResult]
    escalations: list[Escalation]
    rolled_back: bool
    cleanup_deleted: list[str]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(
    repo_root: Path,
    applied_count: int,
    *,
    escalations: list[Escalation] | None = None,
    cleanup_paths: list[Path] | None = None,
    config_path: Path | None = None,
    write_report: bool = True,
) -> ValidationReport:
    """Run configured validation commands; rollback on failure.

    Parameters
    ----------
    repo_root:
        Root of the repository being validated.
    applied_count:
        Number of rope history entries to undo if validation fails.
    escalations:
        Escalations from the apply phase; passed through verbatim.
    cleanup_paths:
        Extra paths to delete on failure (e.g. generated shim files).
    config_path:
        Path to the TOML config file.  Defaults to ``repo_root/refactor.toml``.
    write_report:
        When True, write ``repo_root/.refactor_plan/validation_report.json``.
    """
    if escalations is None:
        escalations = []
    if cleanup_paths is None:
        cleanup_paths = []
    if config_path is None:
        config_path = repo_root / "refactor.toml"

    commands, fail_fast = _load_config(config_path)

    # Auto-append lint-imports if .importlinter exists and not already present
    if (repo_root / ".importlinter").exists() and "lint-imports" not in commands:
        commands.append("lint-imports")

    results: list[ValidationCommandResult] = []
    passed = True

    for cmd in commands:
        result = _run_command(cmd, repo_root)
        results.append(result)
        if result.exit_code != 0:
            passed = False
            if fail_fast:
                break

    rolled_back = False
    cleanup_deleted: list[str] = []

    if not passed:
        log.info("Validation failed — invoking rollback (applied_count=%d)", applied_count)
        rollback(repo_root, applied_count)
        rolled_back = True

        for path in cleanup_paths:
            if path.exists():
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    cleanup_deleted.append(str(path.relative_to(repo_root)))
                except Exception as exc:
                    log.warning("Could not delete cleanup path %s: %s", path, exc)

    report = ValidationReport(
        passed=passed,
        commands=results,
        escalations=escalations,
        rolled_back=rolled_back,
        cleanup_deleted=cleanup_deleted,
    )

    if write_report:
        out_dir = repo_root / ".refactor_plan"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "validation_report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config(config_path: Path) -> tuple[list[str], bool]:
    """Return (commands, fail_fast).  Missing file or missing section → ([], True)."""
    if not config_path.exists():
        return [], True
    with config_path.open("rb") as fh:
        data = tomllib.load(fh)
    section = data.get("validate", {})
    if not section:
        return [], True
    commands = list(section.get("commands", []))
    fail_fast = bool(section.get("fail_fast", True))
    return commands, fail_fast


def _run_command(command: str, cwd: Path) -> ValidationCommandResult:
    """Run *command* in *cwd* and return a ValidationCommandResult."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    duration = time.perf_counter() - t0
    return ValidationCommandResult(
        command=command,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_seconds=duration,
    )
