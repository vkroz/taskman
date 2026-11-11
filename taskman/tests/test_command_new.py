"""Tests for the new and finalize commands."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from tests import sql_helpers as sql

runner = CliRunner()


# ---- top-level creation ----


def _row(key: str) -> dict:
    """The item's ``list --json`` row, which carries its slug, priority and parent."""
    return next(row for row in json.loads(sql.run("list", "--json")) if row["id"] == key)


def test_new_top_level_creates_backlog_draft(sqlite_env) -> None:
    out = sql.run("new", "--title", "Hello", "--type", "task", "--project", "TM")
    assert out.strip() == "sqlite:TM-1"
    fm = sql.show("TM-1")["frontmatter"]
    assert fm["type"] == "task"
    assert fm["status"] == "backlog"
    assert _row("TM-1")["slug"] == "hello"


def test_new_top_level_frontmatter(sqlite_env) -> None:
    key = sql.new("World", "epic")
    data = sql.show(key)
    fm = data["frontmatter"]
    assert fm["id"] == key
    assert fm["project"] == "TM"
    assert fm["type"] == "epic"
    assert fm["status"] == "backlog"
    assert fm["title"] == "World"
    assert fm["draft"] is True
    assert data["body"].startswith("# World")
    row = _row(key)
    assert row["slug"] == "world"
    assert row["priority"] == "00"


# ---- nested creation ----


def test_new_child_under_parent(sqlite_env) -> None:
    epic = sql.new("Epic Root", "epic")
    first = sql.new("First", parent=epic)
    second = sql.new("Second", parent=epic)
    assert _row(first)["parent_id"] == epic
    assert _row(second)["parent_id"] == epic
    assert sql.show(first)["frontmatter"]["project"] == "TM"


# ---- task = leaf rejection ----


def test_new_rejects_task_as_parent(sqlite_env) -> None:
    task = sql.new("Leaf")
    out = sql.run("new", "--title", "Sub", "--type", "task", "--parent", task, code=1)
    assert "task = leaf" in out


# ---- bad type ----


def test_new_rejects_bad_type(sqlite_env) -> None:
    out = sql.run("new", "--title", "X", "--type", "story", "--project", "TM", code=2)
    assert "must be one of" in out


# ---- missing parent ----


def test_new_rejects_missing_parent(sqlite_env) -> None:
    out = sql.run("new", "--title", "X", "--type", "task", "--parent", "TM-99", code=1)
    assert "parent not found" in out


# ---- key allocation ----


def test_new_allocates_sequential_keys(sqlite_env) -> None:
    assert [sql.new("A"), sql.new("B")] == ["TM-1", "TM-2"]


# ---- finalize ----


def test_finalize_clears_draft(sqlite_env) -> None:
    key = sql.new("X")
    assert sql.show(key)["frontmatter"]["draft"] is True
    sql.run("finalize", key)
    assert sql.show(key)["frontmatter"]["draft"] is False


def test_finalize_missing_id(sqlite_env) -> None:
    assert "not found" in sql.run("finalize", "TM-99", code=1)


def test_finalize_parent_item(sqlite_env) -> None:
    parent = sql.new("Parent", "epic")
    sql.new("Child", parent=parent)
    sql.run("finalize", parent)
    assert sql.show(parent)["frontmatter"]["draft"] is False
