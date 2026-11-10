"""Tests for the close command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests import sql_helpers as sql
from tests.workflow_helpers import prepare
from taskman.model.names import parse_name

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_BACKEND": "file", "TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str = "task", parent: str | None = None) -> str:
    args = ["new", "--title", title, "--type", item_type]
    if parent is not None:
        args += ["--parent", parent]
    r = runner.invoke(app, args, env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


def test_close_leaf_task_marks_done(sqlite_env) -> None:
    key = sql.new("Foo")
    sql.prepare(key)
    assert sql.run("close", key).strip() == f"sqlite:{key}"
    assert sql.show(key)["frontmatter"]["status"] == "done"


def test_close_epic_with_all_done_children(sqlite_env) -> None:
    """An epic closes when every child is in done."""
    epic = sql.new("E", "epic")
    child = sql.new("Child", parent=epic)
    sql.prepare(epic)
    sql.prepare(child)
    sql.run("close", child)
    sql.prepare(epic)
    sql.run("close", epic)
    assert sql.show(epic)["frontmatter"]["status"] == "done"


def test_close_epic_rejects_when_child_not_done(sqlite_env) -> None:
    epic = sql.new("E", "epic")
    child = sql.new("Child", parent=epic)
    sql.prepare(epic)
    sql.prepare(child)
    out = sql.run("close", epic, code=1)
    assert "descendants not in done" in out
    assert f"  {child}" in out
    assert sql.show(epic)["frontmatter"]["status"] == "backlog"


def test_close_nested_item_keeps_parent(sqlite_env) -> None:
    """Closing a nested item changes only its own status; it keeps its parent."""
    epic = sql.new("E", "epic")
    child = sql.new("Child", parent=epic)
    sql.prepare(epic)
    sql.prepare(child)
    sql.run("close", child)
    assert sql.show(child)["frontmatter"]["status"] == "done"
    assert sql.show(epic)["frontmatter"]["status"] == "backlog"
    rows = {row["id"]: row for row in json.loads(sql.run("list", "--json"))}
    assert rows[child]["parent_id"] == epic


def test_close_already_done_is_noop(sqlite_env) -> None:
    key = sql.new("Foo")
    sql.prepare(key)
    sql.run("close", key)
    assert "No change" in sql.run("close", key)


def test_close_missing_id(sqlite_env) -> None:
    assert "not found" in sql.run("close", "TM-99", code=1)


# ---- file backend: layout only; removed with 261110001 ----


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
