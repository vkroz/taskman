"""Tests for the list command."""
from __future__ import annotations

import json

from tests import sql_helpers as sql


def _new(title: str, item_type: str = "task") -> str:
    key = sql.new(title, item_type)
    sql.prepare(key)
    return key


def test_list_empty(sqlite_env) -> None:
    assert "no items" in sql.run("list")


def test_list_single_item(sqlite_env) -> None:
    key = _new("Foo", "task")
    out = sql.run("list")
    assert "BACKLOG" in out
    assert key in out
    assert "foo" in out
    assert "task" in out


def test_list_groups_by_status(sqlite_env) -> None:
    key = _new("A", "task")
    _new("B", "task")
    sql.run("move", key, "active")
    out = sql.run("list")
    assert "BACKLOG" in out
    assert "ACTIVE" in out


def test_list_filter_by_status(sqlite_env) -> None:
    key = _new("A", "task")
    other = _new("B", "task")
    sql.run("move", key, "active")
    out = sql.run("list", "--status", "active")
    assert "ACTIVE" in out
    assert "BACKLOG" not in out
    assert other not in out


def test_list_filter_by_type(sqlite_env) -> None:
    _new("An Epic", "epic")
    _new("A Task", "task")
    out = sql.run("list", "--type", "epic")
    assert "an_epic" in out
    assert "a_task" not in out


def test_list_json_output(sqlite_env) -> None:
    key = _new("Foo", "task")
    data = json.loads(sql.run("list", "--json"))
    assert isinstance(data, list)
    assert len(data) == 1
    row = data[0]
    assert row["id"] == key
    assert row["path"] == f"sqlite:{key}"
    assert row["type"] == "task"
    assert row["status"] == "backlog"
    assert row["slug"] == "foo"
    assert row["title"] == "Foo"


def test_list_json_rows_have_draft_and_dependencies_like_ready_rows(sqlite_env) -> None:
    finalized = _new("Finalized")
    draft = sql.new("Draft")
    sql.run("depend", draft, "--on", finalized)
    rows = {row["id"]: row for row in json.loads(sql.run("list", "--json"))}
    assert (rows[finalized]["draft"], rows[finalized]["depends_on"]) == (False, [])
    assert (rows[draft]["draft"], rows[draft]["depends_on"]) == (True, [finalized])
    assert json.loads(sql.run("ready", "--json")) == [rows[finalized]]


def test_list_json_includes_all_items_in_nested_tree(sqlite_env) -> None:
    epic = _new("E", "epic")
    child = sql.new("Child", parent=epic)
    data = json.loads(sql.run("list", "--json"))
    assert len(data) == 2
    assert sorted(row["type"] for row in data) == ["epic", "task"]
    assert {row["id"]: row["parent_id"] for row in data} == {epic: None, child: epic}
