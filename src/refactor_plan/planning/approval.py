

import json
from pathlib import Path
from .proposal import SymbolMoveProposal
from refactor_plan.server_helpers import _check_circular_import_risks, _load_plan, _plan_path, _repo
from refactor_plan.execution import FileMoveProposal
from .planner import write_plan
from refactor_plan import detect_layout


def approve_moves(moves_json: str, repo: str = "") -> str:
    """Record model-approved file moves into the plan for the next apply.

    moves_json — JSON array of move objects:
      [{"source": "src/pkg/foo.py", "dest": "src/contracts/foo.py"}, ...]

    Files not listed stay where they are.
    Pass [] to clear previously approved moves.
    Validates all sources exist and destinations are within source_root.
    Writes approved file_moves to refactor_plan.json.
    Call apply next to execute.
    """
    root = _repo(repo)
    try:
        raw_moves: list[dict] = json.loads(moves_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    plan = _load_plan(root)
    layout = detect_layout(root)

    if not raw_moves:
        plan.file_moves = []
        write_plan(plan, _plan_path(root))
        return "Approved moves cleared."

    proposals: list[FileMoveProposal] = []
    errors: list[str] = []

    for entry in raw_moves:
        src = entry.get("source", "")
        dest = entry.get("dest", "")
        if not src or not dest:
            errors.append(f"  Missing source or dest in: {entry}")
            continue
        src_path = (root / src).resolve() if not Path(src).is_absolute() else Path(src).resolve()
        dest_path = (root / dest).resolve() if not Path(dest).is_absolute() else Path(dest).resolve()
        if not src_path.exists():
            errors.append(f"  Source not found: {src}")
            continue
        try:
            dest_path.relative_to(layout.source_root.resolve())
        except ValueError:
            errors.append(f"  Destination outside source_root ({layout.source_root}): {dest}")
            continue
        proposals.append(FileMoveProposal(
            source=str(src_path),
            dest=str(dest_path),
            dest_package=str(dest_path.parent),
        ))

    if errors:
        return "Validation errors — no moves written:\n" + "\n".join(errors)

    plan.file_moves = proposals
    write_plan(plan, _plan_path(root))

    placement_needed = sum(1 for d in plan.pending_decisions if d.needs_placement)
    return (
        f"{len(proposals)} move(s) approved and written to plan.\n"
        f"Pending placement decisions: {placement_needed}\n"
        "Call apply next to execute in a sandbox."
    )




def approve_symbol_moves(moves_json: str, repo: str = "") -> str:
    """Mark symbol moves as approved for the next apply.

    moves_json — JSON array of move objects:
      [{"source": "src/pkg/foo.py", "dest": "src/other/bar.py", "symbol": "MyClass"}, ...]

    Accepts any valid source/dest/symbol triple — not limited to planner proposals.
    Validates that the source file exists and contains the named symbol.
    Pass [] to clear all approved symbol moves.
    Call apply next to execute approved moves in a sandbox.
    """
    root = _repo(repo)
    try:
        raw_moves: list[dict] = json.loads(moves_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"

    plan = _load_plan(root)

    if not raw_moves:
        for m in plan.symbol_moves:
            m.approved = False
        write_plan(plan, _plan_path(root))
        return "Symbol move approvals cleared."
    import ast

    proposals: list[SymbolMoveProposal] = []
    errors: list[str] = []

    for entry in raw_moves:
        src = entry.get("source", "")
        dest = entry.get("dest", "")
        sym = entry.get("symbol", "")
        if not src or not dest or not sym:
            errors.append(f"  Missing source, dest, or symbol in: {entry}")
            continue
        src_path = (root / src).resolve() if not Path(src).is_absolute() else Path(src).resolve()
        dest_path = (root / dest).resolve() if not Path(dest).is_absolute() else Path(dest).resolve()
        if not src_path.exists():
            errors.append(f"  Source not found: {src}")
            continue
        # Verify symbol exists in source file
        try:
            tree = ast.parse(src_path.read_text(encoding="utf-8"))
            names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            if sym not in names:
                errors.append(f"  Symbol '{sym}' not found in {src_path}")
                continue
        except SyntaxError as exc:
            errors.append(f"  Cannot parse {src_path}: {exc}")
            continue
        # Pre-flight: circular import check (hard block per spec)
        risks = _check_circular_import_risks(src_path, dest_path, sym, root)
        if risks:
            errors.append(f"  Circular import risk for '{sym}':\n    " + "\n    ".join(risks))
            continue
        proposals.append(SymbolMoveProposal(source=str(src_path), dest=str(dest_path), symbol=sym, approved=True))

    if errors:
        return "Validation errors — no moves written:\n" + "\n".join(errors)

    # Merge with existing unapproved planner proposals (keep them for reference)
    approved_keys = {(m.source, m.dest, m.symbol) for m in proposals}
    retained = [m for m in plan.symbol_moves if (m.source, m.dest, m.symbol) not in approved_keys]
    plan.symbol_moves = retained + proposals
    write_plan(plan, _plan_path(root))

    return (
        f"{len(proposals)} symbol move(s) approved.\n"
        "Call apply next to execute in a sandbox."
    )
