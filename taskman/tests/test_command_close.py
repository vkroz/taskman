"""Tests for the close command."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests.workflow_helpers import prepare
from taskman.model.names import parse_name
from taskman.model.yaml_io import read_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str = "task", parent: str | None = None) -> str:
    args = ["new", "--title", title, "--type", item_type]
    if parent is not None:
        args += ["--parent", parent]
    r = runner.invoke(app, args, env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


def test_close_leaf_task_moves_to_done(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "task")
    r = runner.invoke(app, ["close", item_id], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    new_path = Path(r.output.strip())
    assert new_path.parent.name == "done"
    wi = parse_name(new_path.name)
    assert wi.status == "done"
    data, _ = read_file(new_path)
    assert data["status"] == "done"


def test_close_epic_with_all_done_children(tmp_path: Path) -> None:
    """An epic closes when every child is in done."""
    epic_id = _new(tmp_path, "E", "epic")
    child_id = _new(tmp_path, "Child", "task", parent=epic_id)
    # Close child first (nested item — stays in parent's directory).
    r1 = runner.invoke(app, ["close", child_id], env=_env(tmp_path))
    assert r1.exit_code == 0, r1.output
    prepare(tmp_path, epic_id)
    # Now close epic — should succeed.
    r2 = runner.invoke(app, ["close", epic_id], env=_env(tmp_path))
    assert r2.exit_code == 0, r2.output
    new_path = Path(r2.output.strip())
    assert new_path.parent.name == "done"
    assert new_path.is_dir()


def test_close_epic_rejects_when_child_not_done(tmp_path: Path) -> None:
    epic_id = _new(tmp_path, "E", "epic")
    _new(tmp_path, "Child", "task", parent=epic_id)
    r = runner.invoke(app, ["close", epic_id], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "descendants not in done" in r.output


def test_close_nested_item_stays_in_parent_dir(tmp_path: Path) -> None:
    """Closing a nested item rewrites its name but keeps it in the parent dir."""
    epic_id = _new(tmp_path, "E", "epic")
    child_id = _new(tmp_path, "Child", "task", parent=epic_id)
    r = runner.invoke(app, ["close", child_id], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    new_path = Path(r.output.strip())
    # Child should still be inside the epic's directory, but with status=done in name.
    assert new_path.parent.name.endswith(".backlog.e")  # epic's name unchanged
    wi = parse_name(new_path.name)
    assert wi.status == "done"


def test_close_already_done_is_noop(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo")
    runner.invoke(app, ["close", item_id], env=_env(tmp_path))
    r = runner.invoke(app, ["close", item_id], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "No change" in r.output or "already done" in r.output


def test_close_missing_id(tmp_path: Path) -> None:
    r = runner.invoke(app, ["close", "999999999"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "not found" in r.output
