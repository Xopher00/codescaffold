from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from refactor_plan.planning.proposal import RefactorPlan

_STATE_FILE = "state.json"


def save_state(out_dir: Path, **kwargs: object) -> None:
    """Merge kwargs into out_dir/state.json (creates or updates)."""
    state_path = out_dir / _STATE_FILE
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    for k, v in kwargs.items():
        if v is None:
            state.pop(k, None)
        else:
            state[k] = v
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_state(out_dir: Path) -> dict:
    """Read out_dir/state.json; return {} if missing or unreadable."""
    state_path = out_dir / _STATE_FILE
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _clear_rope_cache(wt_path: Path) -> None:
    """Remove stale rope history from a fresh worktree so rope re-analyses from scratch."""
    rope_dir = wt_path / ".ropeproject"
    for name in ("history", "history.json"):
        p = rope_dir / name
        if p.exists():
            p.unlink()


def create_worktree(repo_root: Path) -> tuple[Path, str]:
    """Create a git worktree on a fresh branch from HEAD; return (worktree_path, branch_name)."""
    ts = int(time.time())
    branch = f"refactor/sandbox-{ts}"
    wt_path = Path(f"/tmp/codescaffold_{ts}")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", str(wt_path), "-b", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")
    _clear_rope_cache(wt_path)
    return wt_path, branch


def create_worktree_from_branch(repo_root: Path, base_branch: str) -> tuple[Path, str]:
    """Create a worktree branching from base_branch instead of HEAD."""
    ts = int(time.time())
    branch = f"refactor/rename-{ts}"
    wt_path = Path(f"/tmp/codescaffold_{ts}")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "-b", branch, str(wt_path), base_branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")
    _clear_rope_cache(wt_path)
    return wt_path, branch


def commit_and_release(repo_root: Path, wt_path: Path, message: str) -> None:
    """Stage all changes in worktree, commit, then remove the worktree directory.

    The branch is kept so the caller can review and merge it.
    """
    subprocess.run(
        ["git", "-C", str(wt_path), "add", "-A"],
        check=True, capture_output=True,
    )
    r = subprocess.run(
        ["git", "-C", str(wt_path), "commit", "-m", message],
        capture_output=True, text=True,
    )
    if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
        raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True,
    )


def discard_worktree(repo_root: Path, wt_path: Path, branch: str) -> None:
    """Remove the worktree directory and delete the branch — discards all changes."""
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "branch", "-D", branch],
        capture_output=True,
    )


def translate_plan(plan: RefactorPlan, old_root: Path, new_root: Path) -> RefactorPlan:
    """Return a copy of plan with all absolute paths rewritten from old_root to new_root."""
    raw = plan.model_dump_json()
    return RefactorPlan.model_validate_json(raw.replace(str(old_root), str(new_root)))
