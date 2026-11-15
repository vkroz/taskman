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


def test_list_text_marks_drafts_in_the_priority_column(sqlite_env) -> None:
    finalized = _new("Finalized")
    draft = sql.new("Draft")
    lines = sql.run("list").splitlines()
    assert f"  00 task  {finalized} finalized                      Finalized" in lines
    assert f"  draft task  {draft} draft                          Draft" in lines


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


def _text_keys(*args: str) -> list[str]:
    """The keys of the text rows, in output order."""
    return [line.split()[2] for line in sql.run("list", *args).splitlines() if line.startswith("  ")]


def _json_keys(*args: str) -> list[str]:
    return [row["id"] for row in json.loads(sql.run("list", *args, "--json"))]


def test_list_follows_the_order_set_with_reorder(sqlite_env) -> None:
    epic = sql.new("Epic", "epic")
    feat = sql.new("Feature", "feat", parent=epic)
    first, second, third = (sql.new(title, parent=feat) for title in ("First", "Second", "Third"))
    other = sql.new("Other epic", "epic")
    other_child = sql.new("Other child", parent=other)
    assert _json_keys() == [epic, feat, first, second, third, other, other_child]
    sql.run("reorder", third, "--position", "1")
    sql.run("reorder", other, "--position", "1")
    # Each parent comes before its children; siblings follow their position.
    order = [other, other_child, epic, feat, third, first, second]
    assert _json_keys() == order
    assert _text_keys() == order
    assert _json_keys("--parent", feat) == _text_keys("--parent", feat) == [third, first, second]
    assert _json_keys("--parent", epic, "--recursive") == [feat, third, first, second]


def test_list_text_keeps_the_status_groups_and_orders_each_group_by_position(sqlite_env) -> None:
    first, second, third = (_new(title) for title in ("First", "Second", "Third"))
    sql.run("move", second, "active")
    sql.run("reorder", third, "--position", "1")
    out = sql.run("list")
    assert out.index("[BACKLOG]") < out.index(third) < out.index(first) < out.index("[ACTIVE]") < out.index(second)
    # JSON has no status groups: it is the order of the UI.
    assert _json_keys() == [third, first, second]


def test_list_keeps_the_projects_apart_in_project_order(sqlite_env) -> None:
    tm_first = sql.new("TM first")
    ops = sql.new("OPS first", project="OPS")
    tm_second = sql.new("TM second")
    sql.run("reorder", tm_second, "--position", "1")
    assert _json_keys() == [tm_second, tm_first, ops]
    assert _json_keys("--project", "OPS") == [ops]


def test_list_help_says_how_items_are_ordered(sqlite_env) -> None:
    text = " ".join(sql.run("list", "--help").split())
    assert "Items show in the order that you set in the UI or with `taskman reorder`" in text
    assert "Text output groups the items by status and keeps this order in each group" in text


def test_list_keeps_its_order_when_reorder_is_refused(sqlite_env) -> None:
    first, second = _new("First"), _new("Second")
    sql.run("close", first)
    assert "closed items are immutable" in sql.run("reorder", first, "--position", "2", code=2)
    assert "Position must be between 1 and 2" in sql.run("reorder", second, "--position", "3", code=2)
    assert _json_keys() == [first, second]
