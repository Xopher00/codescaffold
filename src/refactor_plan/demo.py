"""Demo script: run the full pipeline on the committed sample_pkg fixture.

Exit 0 with real output (communities, proposed moves, compileall result).
Usage: python -m refactor_plan.demo
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

from refactor_plan.interface.cluster_view import build_view
from refactor_plan.interface.graph_bridge import ensure_graph
from refactor_plan.planning.planner import plan as build_plan


def main() -> None:
    fixture = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "sample_pkg"
    if not fixture.exists():
        print(f"ERROR: fixture not found at {fixture}", file=sys.stderr)
        sys.exit(1)

    # Work in a temp copy so we don't modify the committed fixture
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "sample_pkg"
        shutil.copytree(fixture, repo)

        graph_path = ensure_graph(repo)
        view = build_view(graph_path)
        refactor_plan = build_plan(view, repo, graph_path)

        n_communities = len(view.file_communities)
        n_file_moves = len(refactor_plan.file_moves)

        proc = subprocess.run(
            ["python", "-m", "compileall", str(repo / "src"), "-q"],
            capture_output=True,
        )
        compile_status = "PASS" if proc.returncode == 0 else "FAIL"

        print(
            f"{n_communities} community/communities detected, "
            f"{n_file_moves} file move(s) proposed, "
            f"compileall: {compile_status}"
        )

        if compile_status == "FAIL":
            print(proc.stderr.decode(), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
