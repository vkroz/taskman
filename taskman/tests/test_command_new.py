"""Tests for the new and finalize commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.new import NewCommandError, slugify
from tests import sql_helpers as sql
from taskman.model.names import parse_name
from taskman.model.yaml_io import read_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_BACKEND": "file", "TASKMAN_TASKS_DIR": str(tasks_dir)}


# ---- slugify ----


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello_world"


def test_slugify_punctuation_replaced() -> None:
    assert slugify("Add depends_on: field!") == "add_depends_on_field"


def test_slugify_collapses_runs() -> None:
    assert slugify("foo--bar") == "foo_bar"


def test_slugify_strips_leading_trailing() -> None:
    assert slugify("--foo--") == "foo"


def test_slugify_rejects_empty_result() -> None:
    with pytest.raises(NewCommandError):
        slugify("!!!")


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


# ---- file backend: layout only; removed with 261110001 ----


def test_new_nested_under_directory_parent(tmp_path: Path) -> None:
    # Create epic (will start as a file).
    r1 = runner.invoke(
        app, ["new", "--title", "Epic Root", "--type", "epic"], env=_env(tmp_path)
    )
    assert r1.exit_code == 0
    epic_path = Path(r1.output.strip())
    epic_id = parse_name(epic_path.name).item_id

    # Create child task. Should auto-convert epic from file to directory.
    r2 = runner.invoke(
        app,
        ["new", "--title", "Child Task", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    assert r2.exit_code == 0, r2.output

    # Epic should now be a directory at the same stem path.
    epic_dir = tmp_path / "backlog" / epic_path.name[:-3]
    assert epic_dir.is_dir()
    # Special file `_<id>.md` holds the epic's original body.
    assert (epic_dir / f"_{epic_id}.md").exists()
    # Child file inside.
    children = [p for p in epic_dir.iterdir() if not p.name.startswith("_")]
    assert len(children) == 1
    child_wi = parse_name(children[0].name)
    assert child_wi.item_type == "task"
    assert child_wi.slug == "child_task"
    # Original file form is gone.
    assert not epic_path.exists()


def test_new_nested_under_already_directory_parent(tmp_path: Path) -> None:
    """Second nested child does not re-convert the parent."""
    r1 = runner.invoke(app, ["new", "--title", "E", "--type", "epic"], env=_env(tmp_path))
    epic_id = parse_name(Path(r1.output.strip()).name).item_id
    # First child triggers conversion.
    runner.invoke(
        app,
        ["new", "--title", "First", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    # Second child uses existing directory.
    r3 = runner.invoke(
        app,
        ["new", "--title", "Second", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    assert r3.exit_code == 0, r3.output
    epic_dir = list((tmp_path / "backlog").iterdir())[0]
    assert epic_dir.is_dir()
    children = [p for p in epic_dir.iterdir() if not p.name.startswith("_")]
    assert len(children) == 2


def test_new_appends_suffix_on_same_slug_same_day(tmp_path: Path) -> None:
    r1 = runner.invoke(app, ["new", "--title", "Foo"], env=_env(tmp_path))
    assert r1.exit_code == 0
    p1 = Path(r1.output.strip())
    r2 = runner.invoke(app, ["new", "--title", "Foo"], env=_env(tmp_path))
    assert r2.exit_code == 0
    p2 = Path(r2.output.strip())
    assert p1.name != p2.name
    wi1 = parse_name(p1.name)
    wi2 = parse_name(p2.name)
    assert wi1.slug == "foo"
    assert wi2.slug == "foo_2"


def test_new_allocates_unique_ids_sequentially(tmp_path: Path) -> None:
    r1 = runner.invoke(app, ["new", "--title", "A"], env=_env(tmp_path))
    r2 = runner.invoke(app, ["new", "--title", "B"], env=_env(tmp_path))
    id1 = parse_name(Path(r1.output.strip()).name).item_id
    id2 = parse_name(Path(r2.output.strip()).name).item_id
    assert id1 != id2
    assert int(id2[6:]) == int(id1[6:]) + 1


def test_finalize_on_directory_item(tmp_path: Path) -> None:
    """finalize works on an item that has been promoted to a directory."""
    r1 = runner.invoke(app, ["new", "--title", "Parent", "--type", "epic"], env=_env(tmp_path))
    parent_id = parse_name(Path(r1.output.strip()).name).item_id
    runner.invoke(
        app,
        ["new", "--title", "Child", "--type", "task", "--parent", parent_id],
        env=_env(tmp_path),
    )
    # Parent is now a directory. Finalize it.
    r3 = runner.invoke(app, ["finalize", parent_id], env=_env(tmp_path))
    assert r3.exit_code == 0, r3.output

    epic_dir = tmp_path / "backlog" / next(
        (tmp_path / "backlog").iterdir()
    ).name
    body_file = epic_dir / f"_{parent_id}.md"
    data, _ = read_file(body_file)
    assert data["draft"] is False
