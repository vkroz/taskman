"""Tests for the search command."""
from __future__ import annotations

import json

from tests import sql_helpers as sql


def _search(query: str) -> list[dict]:
    return json.loads(sql.run("search", query, "--json"))


def test_search_empty_tree(sqlite_env) -> None:
    assert "(no matches)" in sql.run("search", "anything")


def test_search_no_match(sqlite_env) -> None:
    sql.new("Hello World")
    assert "(no matches)" in sql.run("search", "zzzzz")


def test_search_exact_id(sqlite_env) -> None:
    key = sql.new("Hello")
    data = _search(key)
    assert len(data) == 1
    assert data[0]["id"] == key
    assert data[0]["rank"] == 0


def test_search_exact_slug(sqlite_env) -> None:
    sql.new("Hello World")
    data = _search("hello_world")
    assert len(data) == 1
    assert data[0]["rank"] == 1


def test_search_slug_contains(sqlite_env) -> None:
    sql.new("Hello World")
    data = _search("world")
    assert len(data) >= 1
    assert data[0]["rank"] in (2, 3)  # slug contains "world" AND title contains "world"


def test_search_title_token(sqlite_env) -> None:
    sql.new("Search me here")
    # Slug is "search_me_here"; query "Search" hits the title token (case-insensitive).
    data = _search("Search")
    assert len(data) == 1
    # Slug contains "search" (rank 2) > title (rank 3); both valid. Either is acceptable.
    assert data[0]["rank"] in (2, 3)


def test_search_body_token(sqlite_env) -> None:
    key = sql.new("Foo")
    sql.run("update", key, "--body-file", "-", input="# Foo\n\nSome unique_marker text here.\n")
    data = _search("unique_marker")
    assert len(data) == 1
    assert data[0]["id"] == key
    assert data[0]["rank"] == 4


def test_search_ranking_order(sqlite_env) -> None:
    """When the same query matches multiple items at different ranks, the better rank wins."""
    # Item A: title contains "alpha" (body match for "alpha")
    sql.new("Alpha One")
    # Item B: slug exactly "alpha"
    b = sql.new("alpha")
    # Item C: contains "alpha" in body only
    sql.new("Other", body="# Other\n\nalpha in body\n")
    data = _search("alpha")
    # First result should be the exact-slug match (B), rank 1.
    assert data[0]["id"] == b
    assert data[0]["rank"] == 1


def test_search_walks_nested_tree(sqlite_env) -> None:
    """Search finds child items, not just top-level ones."""
    epic = sql.new("Parent Epic", "epic")
    child = sql.new("Nested Child", parent=epic)
    assert child in {row["id"] for row in _search("nested")}


def test_search_human_output(sqlite_env) -> None:
    key = sql.new("Hello")
    output = sql.run("search", "hello")
    assert "hello" in output.lower()
    assert key in output
    # Non-JSON output starts with the rank in brackets.
    assert "[" in output
