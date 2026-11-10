"""Tests for the move command."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests import sql_helpers as sql
from tests.workflow_helpers import prepare
from taskman.model.names import parse_name
from taskman.model.yaml_io import read_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_BACKEND": "file", "TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new_top_level(tmp_path: Path, title: str, item_type: str = "task") -> str:
    """Create a top-level item; return its ID."""
    r = runner.invoke(
        app, ["new", "--title", title, "--type", item_type], env=_env(tmp_path)
    )
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


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


# ---- file backend: layout only; removed with 261110001 ----


def test_move_preserves_subtree(tmp_path: Path) -> None:
    """When a root with children moves, all descendants move with it."""
    epic_id = _new_top_level(tmp_path, "Epic", item_type="epic")
    # Add a child to convert the epic to a directory.
    r = runner.invoke(
        app,
        ["new", "--title", "Child", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    assert r.exit_code == 0
    child_path_before = Path(r.output.strip())
    assert child_path_before.parent.parent.name == "backlog"

    # Move the epic to active.
    r2 = runner.invoke(app, ["move", epic_id, "active"], env=_env(tmp_path))
    assert r2.exit_code == 0, r2.output
    new_epic_dir = Path(r2.output.strip())
    assert new_epic_dir.is_dir()
    assert new_epic_dir.parent.name == "active"

    # Special body file and child are inside, child name unchanged.
    assert (new_epic_dir / f"_{epic_id}.md").exists()
    child_files = [p for p in new_epic_dir.iterdir() if not p.name.startswith("_")]
    assert len(child_files) == 1
    # Child's in-name status remains "backlog" — only the root's status moved.
    child_wi = parse_name(child_files[0].name)
    assert child_wi.status == "backlog"


def test_move_directory_item_updates_special_file(tmp_path: Path) -> None:
    """For a directory item, YAML status lives in _<id>.md inside; verify update."""
    epic_id = _new_top_level(tmp_path, "Epic", item_type="epic")
    runner.invoke(
        app,
        ["new", "--title", "Child", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    runner.invoke(app, ["move", epic_id, "active"], env=_env(tmp_path))
    epic_dir = next((tmp_path / "active").iterdir())
    data, _ = read_file(epic_dir / f"_{epic_id}.md")
    assert data["status"] == "active"
