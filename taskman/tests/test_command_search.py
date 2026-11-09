"""Tests for the search command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from taskman.model.layout import find_item_by_id
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file, write_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str = "task") -> str:
    r = runner.invoke(
        app, ["new", "--title", title, "--type", item_type], env=_env(tmp_path)
    )
    assert r.exit_code == 0, r.output
    return parse_name(Path(r.output.strip()).name).item_id


def _set_body(tmp_path: Path, item_id: str, body: str) -> None:
    path = find_item_by_id(tmp_path, item_id)
    body_file = (
        path / special_file_name(item_id) if path.is_dir() else path
    )
    data, _ = read_file(body_file)
    write_file(body_file, data, body)


def test_search_empty_tree(tmp_path: Path) -> None:
    r = runner.invoke(app, ["search", "anything"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "(no matches)" in r.output


def test_search_no_match(tmp_path: Path) -> None:
    _new(tmp_path, "Hello World")
    r = runner.invoke(app, ["search", "zzzzz"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "(no matches)" in r.output


def test_search_exact_id(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Hello")
    r = runner.invoke(app, ["search", item_id, "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    assert len(data) == 1
    assert data[0]["id"] == item_id
    assert data[0]["rank"] == 0


def test_search_exact_slug(tmp_path: Path) -> None:
    _new(tmp_path, "Hello World")
    r = runner.invoke(app, ["search", "hello_world", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    assert len(data) == 1
    assert data[0]["rank"] == 1


def test_search_slug_contains(tmp_path: Path) -> None:
    _new(tmp_path, "Hello World")
    r = runner.invoke(app, ["search", "world", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    assert len(data) >= 1
    assert data[0]["rank"] in (2, 3)  # slug contains "world" AND title contains "world"


def test_search_title_token(tmp_path: Path) -> None:
    _new(tmp_path, "Search me here")
    # Slug is "search_me_here"; query "Search" hits the title token (case-insensitive).
    r = runner.invoke(app, ["search", "Search", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    assert len(data) == 1
    # Slug contains "search" (rank 2) > title (rank 3); both valid. Either is acceptable.
    assert data[0]["rank"] in (2, 3)


def test_search_body_token(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo")
    _set_body(tmp_path, item_id, "# Foo\n\nSome unique_marker text here.\n")
    r = runner.invoke(app, ["search", "unique_marker", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    assert len(data) == 1
    assert data[0]["rank"] == 4


def test_search_ranking_order(tmp_path: Path) -> None:
    """When the same query matches multiple items at different ranks, the better rank wins."""
    # Item A: title contains "alpha" (body match for "alpha")
    a = _new(tmp_path, "Alpha One")
    # Item B: slug exactly "alpha"
    b = _new(tmp_path, "alpha")
    # Item C: contains "alpha" in body only
    c = _new(tmp_path, "Other")
    _set_body(tmp_path, c, "# Other\n\nalpha in body\n")
    r = runner.invoke(app, ["search", "alpha", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    # First result should be the exact-slug match (B), rank 1.
    assert data[0]["id"] == b
    assert data[0]["rank"] == 1


def test_search_walks_nested_tree(tmp_path: Path) -> None:
    """Search finds items inside directory items, not just top-level."""
    epic_id = _new(tmp_path, "Parent Epic", "epic")
    r = runner.invoke(
        app,
        ["new", "--title", "Nested Child", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    assert r.exit_code == 0
    child_id = parse_name(Path(r.output.strip()).name).item_id
    r = runner.invoke(app, ["search", "nested", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    ids = {row["id"] for row in data}
    assert child_id in ids


def test_search_human_output(tmp_path: Path) -> None:
    _new(tmp_path, "Hello")
    r = runner.invoke(app, ["search", "hello"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "hello" in r.output.lower()
    # Non-JSON output starts with the rank in brackets.
    assert "[" in r.output
