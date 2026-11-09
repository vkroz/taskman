"""Tests for the new and finalize commands."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.new import NewCommandError, slugify
from taskman.model.names import parse_name
from taskman.model.yaml_io import read_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


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


def test_new_top_level_creates_file_in_backlog(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["new", "--title", "Hello", "--type", "task"], env=_env(tmp_path)
    )
    assert result.exit_code == 0, result.output
    created = list((tmp_path / "backlog").glob("*"))
    assert len(created) == 1
    f = created[0]
    assert f.is_file()
    wi = parse_name(f.name)
    assert wi.item_type == "task"
    assert wi.status == "backlog"
    assert wi.slug == "hello"


def test_new_top_level_frontmatter(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["new", "--title", "World", "--type", "epic"], env=_env(tmp_path)
    )
    assert result.exit_code == 0, result.output
    path = Path(result.output.strip())
    data, body = read_file(path)
    assert data["type"] == "epic"
    assert data["status"] == "backlog"
    assert data["slug"] == "world"
    assert data["priority"] == "00"
    assert data["title"] == "World"
    assert data["draft"] is True
    assert data["id"]
    assert body.startswith("# World")


# ---- nested creation under directory parent ----


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


# ---- task = leaf rejection ----


def test_new_rejects_task_as_parent(tmp_path: Path) -> None:
    r1 = runner.invoke(app, ["new", "--title", "Leaf", "--type", "task"], env=_env(tmp_path))
    task_id = parse_name(Path(r1.output.strip()).name).item_id
    r2 = runner.invoke(
        app,
        ["new", "--title", "Sub", "--type", "task", "--parent", task_id],
        env=_env(tmp_path),
    )
    assert r2.exit_code != 0
    assert "task = leaf" in r2.output


# ---- bad type ----


def test_new_rejects_bad_type(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["new", "--title", "X", "--type", "story"], env=_env(tmp_path)
    )
    assert result.exit_code != 0
    assert "must be one of" in result.output


# ---- missing parent ----


def test_new_rejects_missing_parent(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["new", "--title", "X", "--type", "task", "--parent", "999999999"],
        env=_env(tmp_path),
    )
    assert result.exit_code != 0
    assert "parent not found" in result.output


# ---- slug uniqueness ----


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


# ---- ID uniqueness ----


def test_new_allocates_unique_ids_sequentially(tmp_path: Path) -> None:
    r1 = runner.invoke(app, ["new", "--title", "A"], env=_env(tmp_path))
    r2 = runner.invoke(app, ["new", "--title", "B"], env=_env(tmp_path))
    id1 = parse_name(Path(r1.output.strip()).name).item_id
    id2 = parse_name(Path(r2.output.strip()).name).item_id
    assert id1 != id2
    assert int(id2[6:]) == int(id1[6:]) + 1


# ---- finalize ----


def test_finalize_clears_draft(tmp_path: Path) -> None:
    r1 = runner.invoke(app, ["new", "--title", "X"], env=_env(tmp_path))
    assert r1.exit_code == 0
    path = Path(r1.output.strip())
    item_id = parse_name(path.name).item_id
    data, _ = read_file(path)
    assert data["draft"] is True

    r2 = runner.invoke(app, ["finalize", item_id], env=_env(tmp_path))
    assert r2.exit_code == 0, r2.output

    data2, _ = read_file(path)
    assert data2["draft"] is False


def test_finalize_missing_id(tmp_path: Path) -> None:
    result = runner.invoke(app, ["finalize", "999999999"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "not found" in result.output


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
