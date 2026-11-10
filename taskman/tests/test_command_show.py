"""Tests for the show command."""
from __future__ import annotations

from tests import sql_helpers as sql


def test_show_existing_item(sqlite_env) -> None:
    key = sql.new("Foo Bar")
    out = sql.run("show", key)
    assert "title: Foo Bar" in out
    assert "# Foo Bar" in out


def test_show_missing_id(sqlite_env) -> None:
    assert "not found" in sql.run("show", "TM-99", code=1)


def test_show_parent_item(sqlite_env) -> None:
    """show works on items that have children."""
    parent = sql.new("Parent", "epic")
    sql.new("Child", parent=parent)
    assert "title: Parent" in sql.run("show", parent)


def test_show_json_output(sqlite_env) -> None:
    key = sql.new("Json Test", "feat")
    data = sql.show(key)
    assert data["path"] == f"sqlite:{key}"
    assert data["frontmatter"]["title"] == "Json Test"
    assert data["frontmatter"]["type"] == "feat"
    assert data["body"].startswith("# Json Test")
