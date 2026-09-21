"""Tests for the convert command."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests.workflow_helpers import prepare
from taskman.model.names import parse_name
from taskman.model.yaml_io import read_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str, parent: str | None = None) -> str:
    args = ["new", "--title", title, "--type", item_type]
    if parent is not None:
        args += ["--parent", parent]
    r = runner.invoke(app, args, env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


def test_convert_epic_to_feat_on_file_form(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "epic")
    r = runner.invoke(app, ["convert", item_id, "feat"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    new_path = Path(r.output.strip())
    wi = parse_name(new_path.name)
    assert wi.item_type == "feat"
    data, _ = read_file(new_path)
    assert data["type"] == "feat"


def test_convert_feat_to_task_on_leaf_succeeds(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "feat")
    r = runner.invoke(app, ["convert", item_id, "task"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    new_path = Path(r.output.strip())
    assert parse_name(new_path.name).item_type == "task"


def test_convert_feat_to_task_with_children_rejected(tmp_path: Path) -> None:
    epic_id = _new(tmp_path, "E", "epic")
    _new(tmp_path, "Child", "task", parent=epic_id)
    # Try converting the epic (now a dir) to task.
    r = runner.invoke(app, ["convert", epic_id, "task"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "has children" in r.output


def test_convert_epic_to_feat_on_dir_form_succeeds(tmp_path: Path) -> None:
    """epic↔feat on a directory item is allowed — only →task is constrained."""
    epic_id = _new(tmp_path, "E", "epic")
    _new(tmp_path, "Child", "task", parent=epic_id)
    r = runner.invoke(app, ["convert", epic_id, "feat"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    new_path = Path(r.output.strip())
    assert new_path.is_dir()
    wi = parse_name(new_path.name)
    assert wi.item_type == "feat"
    # Special file body still readable, type updated.
    data, _ = read_file(new_path / f"_{epic_id}.md")
    assert data["type"] == "feat"


def test_convert_task_to_feat(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "task")
    r = runner.invoke(app, ["convert", item_id, "feat"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    assert parse_name(Path(r.output.strip()).name).item_type == "feat"


def test_convert_task_to_epic(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "task")
    r = runner.invoke(app, ["convert", item_id, "epic"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    assert parse_name(Path(r.output.strip()).name).item_type == "epic"


def test_convert_same_type_is_noop(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "epic")
    r = runner.invoke(app, ["convert", item_id, "epic"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "No change" in r.output


def test_convert_missing_id(tmp_path: Path) -> None:
    r = runner.invoke(app, ["convert", "999999999", "feat"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "not found" in r.output


def test_convert_bad_target_type(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo", "epic")
    r = runner.invoke(app, ["convert", item_id, "story"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "must be one of" in r.output


def test_convert_preserves_status_and_priority(tmp_path: Path) -> None:
    """Convert changes only the type token, not status or priority."""
    item_id = _new(tmp_path, "Foo", "epic")
    runner.invoke(app, ["move", item_id, "active"], env=_env(tmp_path))
    r = runner.invoke(app, ["convert", item_id, "feat"], env=_env(tmp_path))
    new_path = Path(r.output.strip())
    wi = parse_name(new_path.name)
    assert wi.status == "active"
    assert wi.priority == "00"
    assert wi.item_type == "feat"
