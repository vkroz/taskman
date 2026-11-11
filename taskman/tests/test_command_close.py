"""Tests for the close command."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from tests import sql_helpers as sql

runner = CliRunner()


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
