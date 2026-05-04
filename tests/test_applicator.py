from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
import rope.base.project as rp
from refactor_plan.applicator.cleanup import ensure_future_annotations, find_stray_inits, is_residue
from refactor_plan.applicator.file_moves import apply_file_move
from refactor_plan.applicator.symbol_moves import _organize_imports
from refactor_plan.applicator.symbol_moves import apply_symbol_move
from refactor_plan.naming.namer import RenameEntry, RenameMap
from refactor_plan.planning.planner import RefactorPlan
from refactor_plan.naming.name_apply import apply_rename_map
from refactor_plan.execution.apply import apply_plan
from refactor_plan.execution.import_rewrites import MoveRecord, rewrite_cross_cluster_imports
from refactor_plan.execution.models import ClusterInfo, AppliedAction, ApplyResult, Escalation, MoveKind, MoveStrategy
from refactor_plan.execution.rope_rename import _find_symbol_offset, rename_module, rename_symbol
from refactor_plan.records.manifests import read_manifest, read_stray_manifest, write_manifest, write_stray_manifest
from refactor_plan.records.rollback import rollback


# ---------------------------------------------------------------------------
# is_residue
# ---------------------------------------------------------------------------

def test_is_residue_true(tmp_path: Path) -> None:
    f = tmp_path / "residue.py"
    f.write_text("from os import path\nfrom sys import argv\n")
    assert is_residue(f) is True


def test_is_residue_false(tmp_path: Path) -> None:
    f = tmp_path / "real.py"
    f.write_text("from os import path\n\ndef do_thing():\n    return path.sep\n")
    assert is_residue(f) is False


def test_is_residue_empty(tmp_path: Path) -> None:
    f = tmp_path / "empty.py"
    f.write_text("")
    assert is_residue(f) is True


def test_is_residue_missing(tmp_path: Path) -> None:
    assert is_residue(tmp_path / "nonexistent.py") is False


# ---------------------------------------------------------------------------
# ensure_future_annotations
# ---------------------------------------------------------------------------

def test_ensure_future_annotations_adds(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("def foo(): pass\n")
    assert ensure_future_annotations(f) is True
    assert "from __future__ import annotations" in f.read_text()


def test_ensure_future_annotations_idempotent(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("from __future__ import annotations\n\ndef foo(): pass\n")
    assert ensure_future_annotations(f) is False


# ---------------------------------------------------------------------------
# find_stray_inits
# ---------------------------------------------------------------------------

def test_find_stray_inits_detects_lonely_init(tmp_path: Path) -> None:
    pkg = tmp_path / "empty_pkg"
    pkg.mkdir()
    init = pkg / "__init__.py"
    init.write_text("")
    assert init in find_stray_inits(tmp_path)


def test_find_stray_inits_ignores_populated_package(tmp_path: Path) -> None:
    pkg = tmp_path / "real_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "module.py").write_text("x = 1\n")
    assert pkg / "__init__.py" not in find_stray_inits(tmp_path)


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------

def test_manifest_roundtrip(tmp_path: Path) -> None:
    result = ApplyResult(applied=[
        AppliedAction(
            kind=MoveKind.FILE,
            source="/src/a.py",
            dest="/dest/a.py",
            strategy=MoveStrategy.ROPE,
            imports_rewritten=1,
        )
    ])
    write_manifest(result, tmp_path)
    recovered = read_manifest(tmp_path)
    assert recovered is not None
    assert recovered.applied[0].source == "/src/a.py"


def test_read_manifest_missing(tmp_path: Path) -> None:
    assert read_manifest(tmp_path) is None


def test_stray_manifest_roundtrip(tmp_path: Path) -> None:
    write_stray_manifest(["a/__init__.py", "b/__init__.py"], tmp_path)
    assert read_stray_manifest(tmp_path) == ["a/__init__.py", "b/__init__.py"]


def test_read_stray_manifest_missing(tmp_path: Path) -> None:
    assert read_stray_manifest(tmp_path) == []


# ---------------------------------------------------------------------------
# import_rewrites
# ---------------------------------------------------------------------------

def test_rewrite_whole_module_move(tmp_path: Path) -> None:
    f = tmp_path / "consumer.py"
    f.write_text("from old.module import Alpha, Beta\n")
    records = [MoveRecord(old_module="old.module", new_module="new.module", symbols=[])]
    assert rewrite_cross_cluster_imports(f, records) is True
    assert "new.module" in f.read_text()
    assert "old.module" not in f.read_text()


def test_rewrite_per_symbol_move(tmp_path: Path) -> None:
    f = tmp_path / "consumer.py"
    f.write_text("from old.module import Alpha, Beta\n")
    records = [MoveRecord(old_module="old.module", new_module="new.module", symbols=["Alpha"])]
    rewrite_cross_cluster_imports(f, records)
    text = f.read_text()
    assert "new.module" in text
    assert "Beta" in text


def test_rewrite_no_match(tmp_path: Path) -> None:
    f = tmp_path / "consumer.py"
    original = "from unrelated.module import Thing\n"
    f.write_text(original)
    records = [MoveRecord(old_module="old.module", new_module="new.module", symbols=[])]
    assert rewrite_cross_cluster_imports(f, records) is False
    assert f.read_text() == original


def test_rewrite_star_import_untouched(tmp_path: Path) -> None:
    f = tmp_path / "consumer.py"
    f.write_text("from old.module import *\n")
    records = [MoveRecord(old_module="old.module", new_module="new.module", symbols=[])]
    assert rewrite_cross_cluster_imports(f, records) is False


# ---------------------------------------------------------------------------
# symbol_moves — depth guard
# ---------------------------------------------------------------------------

def test_symbol_remover_does_not_remove_method_with_same_name(tmp_path: Path) -> None:
    """_SymbolRemover must not remove a class method named target when a top-level target exists."""
    src = tmp_path / "mod.py"
    src.write_text(
        "class Foo:\n"
        "    def target(self):\n"
        "        return 1\n"
        "\n\n"
        "def target():\n"
        "    return 2\n"
    )
    dest = tmp_path / "dest.py"

    action = apply_symbol_move(src, dest, "target", tmp_path)

    assert isinstance(action, AppliedAction)
    src_text = src.read_text()
    # Top-level target removed, Foo.target must remain
    assert "class Foo" in src_text
    assert "def target(self)" in src_text
    assert "def target():" not in src_text


def test_symbol_move_extracts_function(messy_repo: Path) -> None:
    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    dest = messy_repo / "src" / "messy_pkg" / "extracted.py"
    action = apply_symbol_move(src, dest, "helper", messy_repo)
    assert isinstance(action, AppliedAction)
    assert "def helper" not in src.read_text()
    assert "def helper" in dest.read_text()


def test_symbol_move_after_large_function(messy_repo_with_large_func: Path) -> None:
    repo = messy_repo_with_large_func
    src = repo / "src" / "messy_pkg" / "god.py"
    dest = repo / "src" / "messy_pkg" / "dest.py"
    action = apply_symbol_move(src, dest, "small_function", repo)
    assert isinstance(action, AppliedAction)
    assert "def small_function" not in src.read_text()
    assert "def small_function" in dest.read_text()


def test_symbol_move_missing_symbol_returns_escalation(messy_repo: Path) -> None:
    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    dest = messy_repo / "src" / "messy_pkg" / "extracted.py"
    result = apply_symbol_move(src, dest, "nonexistent_func", messy_repo)
    assert isinstance(result, Escalation)
    assert "nonexistent_func" in result.reason


def test_symbol_move_missing_source_returns_escalation(tmp_path: Path) -> None:
    result = apply_symbol_move(tmp_path / "gone.py", tmp_path / "dest.py", "foo", tmp_path)
    assert isinstance(result, Escalation)


def test_symbol_move_stores_snapshot(messy_repo: Path) -> None:
    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    original = src.read_text()
    dest = messy_repo / "src" / "messy_pkg" / "extracted.py"
    action = apply_symbol_move(src, dest, "helper", messy_repo)
    assert isinstance(action, AppliedAction)
    assert action.original_content is not None
    assert action.original_content[str(src)] == original


# ---------------------------------------------------------------------------
# file_moves
# ---------------------------------------------------------------------------

def test_apply_file_move_success(messy_repo: Path) -> None:
    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    dest_pkg = messy_repo / "src" / "messy_pkg" / "sub"
    dest_pkg.mkdir()
    (dest_pkg / "__init__.py").write_text("")
    project = rp.Project(str(messy_repo))
    try:
        result = apply_file_move(project, src, dest_pkg)
    finally:
        project.close()
    assert isinstance(result, AppliedAction)
    assert result.strategy == MoveStrategy.ROPE


def test_apply_file_move_outside_project(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "module.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n")
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = rp.Project(str(project_root))
    try:
        result = apply_file_move(project, outside, project_root)
    finally:
        project.close()
    assert isinstance(result, Escalation)
    assert result.category == "path_resolution"


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

def test_rollback_no_manifest(tmp_path: Path) -> None:
    messages = rollback(tmp_path, tmp_path)
    assert any("No manifest" in m for m in messages)


def test_rollback_libcst_restores_files(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    original_text = "def original(): pass\n"
    target.write_text("# modified\n")
    result = ApplyResult(applied=[
        AppliedAction(
            kind=MoveKind.SYMBOL,
            source=str(target),
            dest=str(tmp_path / "dest.py"),
            symbol="original",
            strategy=MoveStrategy.LIBCST,
            original_content={str(target): original_text},
        )
    ])
    write_manifest(result, tmp_path)
    actions = rollback(tmp_path, tmp_path)
    assert target.read_text() == original_text
    assert any("libcst restore" in a for a in actions)


def test_rollback_no_snapshot_skips_gracefully(tmp_path: Path) -> None:
    result = ApplyResult(applied=[
        AppliedAction(
            kind=MoveKind.SYMBOL,
            source=str(tmp_path / "src.py"),
            dest=str(tmp_path / "dest.py"),
            symbol="foo",
            strategy=MoveStrategy.LIBCST,
            original_content=None,
        )
    ])
    write_manifest(result, tmp_path)
    # Must not raise AssertionError
    actions = rollback(tmp_path, tmp_path)
    assert isinstance(actions, list)


# ---------------------------------------------------------------------------
# rollback — empty-dict snapshot and rope-failure summary
# ---------------------------------------------------------------------------

def test_rollback_empty_dict_snapshot_skips_gracefully(tmp_path: Path) -> None:
    """original_content={} (empty, not None) must not restore any files and not raise."""
    result = ApplyResult(applied=[
        AppliedAction(
            kind=MoveKind.SYMBOL,
            source=str(tmp_path / "src.py"),
            dest=str(tmp_path / "dest.py"),
            symbol="foo",
            strategy=MoveStrategy.LIBCST,
            original_content={},  # empty dict — previously silently dropped before assert
        )
    ])
    write_manifest(result, tmp_path)
    actions = rollback(tmp_path, tmp_path)
    assert isinstance(actions, list)
    assert not any("libcst restore:" in a for a in actions)


def test_rollback_rope_failure_reports_remaining(tmp_path: Path) -> None:
    """When rope undo fails, the message must note how many actions were not attempted."""
    result = ApplyResult(applied=[
        AppliedAction(kind=MoveKind.FILE, source=f"/src/a{i}.py", dest=f"/dest/a{i}.py", strategy=MoveStrategy.ROPE)
        for i in range(3)
    ])
    write_manifest(result, tmp_path)
    actions = rollback(tmp_path, tmp_path)
    failure_msgs = [a for a in actions if "rope undo failed" in a]
    assert failure_msgs, "Expected at least one rope undo failure message"
    # First failure (last applied action, reversed) should report remaining count
    assert any("not attempted" in a for a in failure_msgs)


# ---------------------------------------------------------------------------
# warning coverage — silent failures are observable
# ---------------------------------------------------------------------------

def test_organize_imports_warning_on_out_of_project_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """_organize_imports must log a warning when the file is outside the project root."""
    outside = Path("/tmp/not_in_project.py")
    with caplog.at_level(logging.WARNING, logger="refactor_plan.applicator.symbol_moves"):
        _organize_imports(outside, tmp_path)
    assert any("import organization failed" in r.message for r in caplog.records)


def test_apply_plan_warns_on_import_rewrite_failure(messy_repo: Path, caplog: pytest.LogCaptureFixture) -> None:
    """apply_plan must emit a warning (not silently swallow) when import rewrite fails on a broken file."""
    broken = messy_repo / "src" / "messy_pkg" / "broken.py"
    broken.write_text("this is @@@@ not valid python\n")

    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    dest = messy_repo / "src" / "messy_pkg" / "extracted.py"
    plan = {
        "file_moves": [],
        "symbol_moves": [{"source": str(src), "dest": str(dest), "symbol": "helper"}],
    }
    out_dir = messy_repo / ".refactor_plan"
    with caplog.at_level(logging.WARNING, logger="refactor_plan.applicator.apply"):
        apply_plan(plan, messy_repo, out_dir, dry_run=False)
    assert any("import rewrite failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# apply_plan (integration)
# ---------------------------------------------------------------------------

def test_apply_plan_dry_run(messy_repo: Path) -> None:
    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    original = src.read_text()
    dest_pkg = messy_repo / "src" / "messy_pkg"
    plan = {"file_moves": [{"source": str(src), "dest_package": str(dest_pkg)}], "symbol_moves": []}
    out_dir = messy_repo / ".refactor_plan"
    result = apply_plan(plan, messy_repo, out_dir, dry_run=True)
    assert len(result.applied) == 1
    assert src.read_text() == original


def test_apply_plan_symbol_move_compileall(messy_repo: Path) -> None:
    src = messy_repo / "src" / "messy_pkg" / "utils.py"
    dest = messy_repo / "src" / "messy_pkg" / "extracted.py"
    plan = {
        "file_moves": [],
        "symbol_moves": [{"source": str(src), "dest": str(dest), "symbol": "helper"}],
    }
    out_dir = messy_repo / ".refactor_plan"
    result = apply_plan(plan, messy_repo, out_dir, dry_run=False)
    assert len(result.applied) == 1
    proc = subprocess.run(
        ["python", "-m", "compileall", str(messy_repo / "src")],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()


# ---------------------------------------------------------------------------
# apply_rename_map
# ---------------------------------------------------------------------------

def _pkg_repo(tmp_path: Path) -> tuple[Path, RefactorPlan, RenameMap]:
    """Fixture helper: a repo with pkg_001/ and a caller that imports from it."""
    pkg = tmp_path / "src" / "pkg_001"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("def compute():\n    return 1\n")
    caller = tmp_path / "src" / "main.py"
    caller.write_text("from pkg_001.core import compute\n\nresult = compute()\n")

    plan = RefactorPlan(clusters=[
        ClusterInfo(
            community_id=1,
            source_files=[str(pkg / "core.py")],
            proposed_package=str(pkg),
        )
    ])
    rename_map = RenameMap(entries=[RenameEntry(old_name="pkg_001", new_name="mypackage")])
    return tmp_path, plan, rename_map


def test_apply_rename_map_dry_run(tmp_path: Path) -> None:
    repo, plan, rename_map = _pkg_repo(tmp_path)
    out_dir = repo / ".refactor_plan"
    result = apply_rename_map(rename_map, plan, repo, out_dir, dry_run=True)

    assert len(result.applied) == 1
    assert result.applied[0].source.endswith("pkg_001")
    assert result.applied[0].dest.endswith("mypackage")
    # No filesystem change
    assert (repo / "src" / "pkg_001").exists()
    assert not (repo / "src" / "mypackage").exists()


def test_apply_rename_map_rope(tmp_path: Path) -> None:
    repo, plan, rename_map = _pkg_repo(tmp_path)
    out_dir = repo / ".refactor_plan"
    result = apply_rename_map(rename_map, plan, repo, out_dir, dry_run=False)

    assert len(result.applied) == 1, result.failed
    assert result.applied[0].strategy in (MoveStrategy.ROPE, MoveStrategy.LIBCST)
    # Old directory gone, new one present
    assert not (repo / "src" / "pkg_001").exists()
    assert (repo / "src" / "mypackage").exists()
    # Caller import was rewritten
    caller_text = (repo / "src" / "main.py").read_text()
    assert "pkg_001" not in caller_text
    assert "mypackage" in caller_text


def test_apply_rename_map_unknown_package(tmp_path: Path) -> None:
    repo, plan, _ = _pkg_repo(tmp_path)
    rename_map = RenameMap(entries=[RenameEntry(old_name="pkg_999", new_name="other")])
    out_dir = repo / ".refactor_plan"
    result = apply_rename_map(rename_map, plan, repo, out_dir, dry_run=False)

    assert len(result.failed) == 1
    assert "pkg_999" in result.failed[0].reason


def test_apply_rename_map_missing_directory(tmp_path: Path) -> None:
    repo, plan, rename_map = _pkg_repo(tmp_path)
    # Remove the directory so it no longer exists
    import shutil
    shutil.rmtree(repo / "src" / "pkg_001")
    out_dir = repo / ".refactor_plan"
    result = apply_rename_map(rename_map, plan, repo, out_dir, dry_run=False)

    assert len(result.skipped) == 1


# ---------------------------------------------------------------------------
# _find_symbol_offset
# ---------------------------------------------------------------------------

def test_find_symbol_offset_function() -> None:
    src = "def helper():\n    return 42\n"
    offset = _find_symbol_offset(src, "helper")
    assert offset is not None
    assert src[offset:offset + 6] == "helper"


def test_find_symbol_offset_class() -> None:
    src = "class MyModel:\n    pass\n"
    offset = _find_symbol_offset(src, "MyModel")
    assert offset is not None
    assert src[offset:offset + 7] == "MyModel"


def test_find_symbol_offset_missing() -> None:
    assert _find_symbol_offset("def foo():\n    pass\n", "bar") is None


def test_find_symbol_offset_async_function() -> None:
    src = "async def fetch():\n    pass\n"
    offset = _find_symbol_offset(src, "fetch")
    assert offset is not None
    assert src[offset:offset + 5] == "fetch"


def test_find_symbol_offset_first_when_duplicate() -> None:
    src = "def helper():\n    pass\n\nclass MyClass:\n    def helper(self):\n        pass\n"
    offset = _find_symbol_offset(src, "helper")
    # First definition (top-level function) should win
    assert offset == src.index("helper")


# ---------------------------------------------------------------------------
# rename_symbol
# ---------------------------------------------------------------------------

def test_rename_symbol_dry_run(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("def helper():\n    return 42\n\ndef caller():\n    return helper()\n")
    original = f.read_text()
    action = rename_symbol(tmp_path, f, "helper", "compute", dry_run=True)
    assert not isinstance(action, Escalation)
    assert action.symbol == "helper"
    assert f.read_text() == original  # unchanged


def test_rename_symbol_propagates_cross_file(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "utils.py").write_text("def helper():\n    return 42\n")
    (tmp_path / "pkg" / "main.py").write_text(
        "from pkg.utils import helper\n\ndef run():\n    return helper()\n"
    )
    action = rename_symbol(tmp_path, tmp_path / "pkg" / "utils.py", "helper", "compute")
    assert not isinstance(action, Escalation)
    assert len(action.files_touched) == 2
    assert "pkg_001" not in (tmp_path / "pkg" / "utils.py").read_text()
    utils_text = (tmp_path / "pkg" / "utils.py").read_text()
    main_text = (tmp_path / "pkg" / "main.py").read_text()
    assert "def compute" in utils_text
    assert "helper" not in utils_text
    assert "compute" in main_text
    assert "helper" not in main_text


def test_rename_symbol_missing_symbol(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    pass\n")
    action = rename_symbol(tmp_path, f, "nonexistent", "other")
    assert isinstance(action, Escalation)
    assert "nonexistent" in action.reason


# ---------------------------------------------------------------------------
# rename_module
# ---------------------------------------------------------------------------

def test_rename_module_file(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "utils.py").write_text("def foo():\n    pass\n")
    (tmp_path / "pkg" / "main.py").write_text("from pkg.utils import foo\n")
    action = rename_module(tmp_path, tmp_path / "pkg" / "utils.py", "helpers")
    assert not isinstance(action, Escalation)
    assert (tmp_path / "pkg" / "helpers.py").exists()
    assert not (tmp_path / "pkg" / "utils.py").exists()
    assert "helpers" in (tmp_path / "pkg" / "main.py").read_text()


def test_rename_module_missing_path(tmp_path: Path) -> None:
    action = rename_module(tmp_path, tmp_path / "nonexistent.py", "other")
    assert isinstance(action, Escalation)


def test_rename_module_dry_run(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "utils.py").write_text("def foo():\n    pass\n")
    original = (tmp_path / "pkg" / "utils.py").read_text()
    action = rename_module(tmp_path, tmp_path / "pkg" / "utils.py", "helpers", dry_run=True)
    assert not isinstance(action, Escalation)
    assert (tmp_path / "pkg" / "utils.py").read_text() == original  # unchanged
    assert action.dest.endswith("helpers.py")
