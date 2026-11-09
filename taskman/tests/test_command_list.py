"""Tests for the list command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests.workflow_helpers import prepare
from taskman.model.names import parse_name

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str = "task") -> str:
    r = runner.invoke(
        app, ["new", "--title", title, "--type", item_type], env=_env(tmp_path)
    )
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


def test_list_empty(tmp_path: Path) -> None:
    r = runner.invoke(app, ["list"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "no items" in r.output


def test_list_single_item(tmp_path: Path) -> None:
    _new(tmp_path, "Foo", "task")
    r = runner.invoke(app, ["list"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "BACKLOG" in r.output
    assert "foo" in r.output
    assert "task" in r.output


def test_list_groups_by_status(tmp_path: Path) -> None:
    id1 = _new(tmp_path, "A", "task")
    _new(tmp_path, "B", "task")
    runner.invoke(app, ["move", id1, "active"], env=_env(tmp_path))
    r = runner.invoke(app, ["list"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "BACKLOG" in r.output
    assert "ACTIVE" in r.output


def test_list_filter_by_status(tmp_path: Path) -> None:
    id1 = _new(tmp_path, "A", "task")
    _new(tmp_path, "B", "task")
    runner.invoke(app, ["move", id1, "active"], env=_env(tmp_path))
    r = runner.invoke(app, ["list", "--status", "active"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "ACTIVE" in r.output
    assert "BACKLOG" not in r.output


def test_list_filter_by_type(tmp_path: Path) -> None:
    _new(tmp_path, "An Epic", "epic")
    _new(tmp_path, "A Task", "task")
    r = runner.invoke(app, ["list", "--type", "epic"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "an_epic" in r.output
    assert "a_task" not in r.output


def test_list_json_output(tmp_path: Path) -> None:
    _new(tmp_path, "Foo", "task")
    r = runner.invoke(app, ["list", "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert isinstance(data, list)
    assert len(data) == 1
    row = data[0]
    assert row["type"] == "task"
    assert row["status"] == "backlog"
    assert row["slug"] == "foo"
    assert row["title"] == "Foo"


def test_list_json_includes_all_items_in_nested_tree(tmp_path: Path) -> None:
    epic_id = _new(tmp_path, "E", "epic")
    runner.invoke(
        app,
        ["new", "--title", "Child", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    r = runner.invoke(app, ["list", "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert len(data) == 2
    types = sorted(row["type"] for row in data)
    assert types == ["epic", "task"]
