"""Tests for the move command."""
from __future__ import annotations


from typer.testing import CliRunner

from tests import sql_helpers as sql

runner = CliRunner()


def test_move_backlog_to_active(sqlite_env) -> None:
    key = sql.new("Foo")
    sql.prepare(key)
    assert sql.run("move", key, "active").strip() == f"sqlite:{key}"
    assert sql.show(key)["frontmatter"]["status"] == "active"


def test_move_active_to_done(sqlite_env) -> None:
    key = sql.new("Foo")
    sql.prepare(key)
    sql.run("move", key, "active")
    sql.run("move", key, "done")
    assert sql.show(key)["frontmatter"]["status"] == "done"


def test_move_same_status_is_noop(sqlite_env) -> None:
    key = sql.new("Foo")
    sql.prepare(key)
    assert "No change" in sql.run("move", key, "backlog")


def test_move_missing_id(sqlite_env) -> None:
    assert "not found" in sql.run("move", "TM-99", "active", code=None)


def test_move_bad_target_status(sqlite_env) -> None:
    key = sql.new("Foo")
    assert "must be one of" in sql.run("move", key, "pending", code=2)


def test_move_activates_nested_item(sqlite_env) -> None:
    epic = sql.new("Epic", "epic")
    child = sql.new("Child", parent=epic)
    sql.prepare(epic)
    sql.prepare(child)
    sql.run("move", child, "active")
    assert sql.show(child)["frontmatter"]["status"] == "active"
    assert sql.show(epic)["frontmatter"]["status"] == "backlog"


def test_moving_a_parent_keeps_child_status(sqlite_env) -> None:
    epic = sql.new("Epic", "epic")
    child = sql.new("Child", parent=epic)
    sql.prepare(epic)
    sql.run("move", epic, "active")
    assert sql.show(epic)["frontmatter"]["status"] == "active"
    assert sql.show(child)["frontmatter"]["status"] == "backlog"
