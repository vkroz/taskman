"""Tests for the convert command."""
from __future__ import annotations

import json
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


def _new(tmp_path: Path, title: str, item_type: str, parent: str | None = None) -> str:
    args = ["new", "--title", title, "--type", item_type]
    if parent is not None:
        args += ["--parent", parent]
    r = runner.invoke(app, args, env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


def _new_sql(title: str, item_type: str, parent: str | None = None) -> str:
    key = sql.new(title, item_type, parent=parent)
    sql.prepare(key)
    return key


def test_convert_epic_to_feat(sqlite_env) -> None:
    key = _new_sql("Foo", "epic")
    assert sql.run("convert", key, "feat").strip() == f"sqlite:{key}"
    assert sql.show(key)["frontmatter"]["type"] == "feat"


def test_convert_feat_to_task_on_leaf_succeeds(sqlite_env) -> None:
    key = _new_sql("Foo", "feat")
    sql.run("convert", key, "task")
    assert sql.show(key)["frontmatter"]["type"] == "task"


def test_convert_feat_to_task_with_children_rejected(sqlite_env) -> None:
    epic = _new_sql("E", "epic")
    child = _new_sql("Child", "task", parent=epic)
    out = sql.run("convert", epic, "task", code=1)
    assert f"has children: {child}" in out
    assert sql.show(epic)["frontmatter"]["type"] == "epic"


def test_convert_epic_with_children_to_feat_succeeds(sqlite_env) -> None:
    """epic↔feat on a parent is allowed — only →task is constrained."""
    epic = _new_sql("E", "epic")
    _new_sql("Child", "task", parent=epic)
    sql.run("convert", epic, "feat")
    assert sql.show(epic)["frontmatter"]["type"] == "feat"


def test_convert_task_to_feat(sqlite_env) -> None:
    key = _new_sql("Foo", "task")
    sql.run("convert", key, "feat")
    assert sql.show(key)["frontmatter"]["type"] == "feat"


def test_convert_task_to_epic(sqlite_env) -> None:
    key = _new_sql("Foo", "task")
    sql.run("convert", key, "epic")
    assert sql.show(key)["frontmatter"]["type"] == "epic"


def test_convert_same_type_is_noop(sqlite_env) -> None:
    key = _new_sql("Foo", "epic")
    assert "No change" in sql.run("convert", key, "epic")


def test_convert_missing_id(sqlite_env) -> None:
    assert "not found" in sql.run("convert", "TM-99", "feat", code=1)


def test_convert_bad_target_type(sqlite_env) -> None:
    key = _new_sql("Foo", "epic")
    assert "must be one of" in sql.run("convert", key, "story", code=2)


def test_convert_preserves_status_and_priority(sqlite_env) -> None:
    """Convert changes only the type, not status or priority."""
    key = _new_sql("Foo", "epic")
    sql.run("move", key, "active")
    sql.run("convert", key, "feat")
    row = next(row for row in json.loads(sql.run("list", "--json")) if row["id"] == key)
    assert row["status"] == "active"
    assert row["priority"] == "00"
    assert row["type"] == "feat"


# ---- file backend: layout only; removed with 261110001 ----


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
