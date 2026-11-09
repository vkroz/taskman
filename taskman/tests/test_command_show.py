"""Tests for the show command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from taskman.model.names import parse_name

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str = "task") -> str:
    r = runner.invoke(
        app, ["new", "--title", title, "--type", item_type], env=_env(tmp_path)
    )
    assert r.exit_code == 0, r.output
    return parse_name(Path(r.output.strip()).name).item_id


def test_show_existing_file_item(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Foo Bar")
    r = runner.invoke(app, ["show", item_id], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "title: Foo Bar" in r.output
    assert "# Foo Bar" in r.output


def test_show_missing_id(tmp_path: Path) -> None:
    r = runner.invoke(app, ["show", "999999999"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "not found" in r.output


def test_show_directory_item(tmp_path: Path) -> None:
    """show works on items that have been promoted to directory form."""
    parent_id = _new(tmp_path, "Parent", "epic")
    runner.invoke(
        app,
        ["new", "--title", "Child", "--type", "task", "--parent", parent_id],
        env=_env(tmp_path),
    )
    r = runner.invoke(app, ["show", parent_id], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "title: Parent" in r.output


def test_show_json_output(tmp_path: Path) -> None:
    item_id = _new(tmp_path, "Json Test", "feat")
    r = runner.invoke(app, ["show", item_id, "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert "frontmatter" in data
    assert "body" in data
    assert "path" in data
    assert data["frontmatter"]["title"] == "Json Test"
    assert data["frontmatter"]["type"] == "feat"
    assert data["body"].startswith("# Json Test")
