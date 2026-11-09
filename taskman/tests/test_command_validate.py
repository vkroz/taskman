"""Tests for the validate command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from taskman.model.names import parse_name

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new_via_cli(tmp_path: Path, title: str, item_type: str = "task") -> str:
    r = runner.invoke(
        app, ["new", "--title", title, "--type", item_type], env=_env(tmp_path)
    )
    assert r.exit_code == 0, r.output
    return parse_name(Path(r.output.strip()).name).item_id


# ---- happy path ----


def test_validate_empty_tree(tmp_path: Path) -> None:
    """Empty tree validates clean."""
    (tmp_path / "backlog").mkdir()
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "OK" in r.output


def test_validate_clean_tree(tmp_path: Path) -> None:
    """A tree built only by `new` validates clean."""
    epic_id = _new_via_cli(tmp_path, "Epic", "epic")
    runner.invoke(
        app,
        ["new", "--title", "Child", "--type", "task", "--parent", epic_id],
        env=_env(tmp_path),
    )
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output


# ---- violations ----


def test_validate_detects_bad_name_grammar(tmp_path: Path) -> None:
    (tmp_path / "backlog").mkdir()
    (tmp_path / "backlog" / "broken_name.md").write_text("---\n---\nbody")
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "bad_name_grammar" in r.output


def test_validate_detects_name_folder_drift(tmp_path: Path) -> None:
    """Top-level item whose in-name status differs from its folder."""
    (tmp_path / "backlog").mkdir()
    # Status says active, but file sits in backlog/.
    p = tmp_path / "backlog" / "00.task-260527001.active.foo.md"
    p.write_text(
        "---\n"
        "id: '260527001'\n"
        "type: task\n"
        "status: active\n"
        "priority: '00'\n"
        "slug: foo\n"
        "title: Foo\n"
        "---\n\nbody\n"
    )
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "name_folder_drift" in r.output


def test_validate_detects_name_yaml_drift(tmp_path: Path) -> None:
    """Frontmatter status disagrees with in-name token."""
    item_id = _new_via_cli(tmp_path, "Foo")
    # Manually corrupt frontmatter status.
    path = next((tmp_path / "backlog").iterdir())
    text = path.read_text()
    # Replace YAML status: backlog with status: active (but folder & name still backlog).
    text = text.replace("status: backlog", "status: active")
    path.write_text(text)
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "name_yaml_drift" in r.output


def test_validate_detects_missing_body(tmp_path: Path) -> None:
    """A directory item without its _<id>.md special file."""
    (tmp_path / "backlog").mkdir()
    epic_dir = tmp_path / "backlog" / "00.epic-260527000.backlog.foo"
    epic_dir.mkdir()
    # No _260527000.md inside.
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "missing_body" in r.output


def test_validate_detects_task_not_leaf(tmp_path: Path) -> None:
    """A task item in directory form violates the leaf invariant."""
    (tmp_path / "backlog").mkdir()
    bad = tmp_path / "backlog" / "00.task-260527001.backlog.foo"
    bad.mkdir()
    (bad / "_260527001.md").write_text(
        "---\nid: '260527001'\ntype: task\nstatus: backlog\npriority: '00'\nslug: foo\n---\nbody\n"
    )
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "task_not_leaf" in r.output


def test_validate_detects_orphan_parent(tmp_path: Path) -> None:
    """A child file inside a non-parseable directory."""
    (tmp_path / "backlog").mkdir()
    bad_parent = tmp_path / "backlog" / "not_a_workitem"
    bad_parent.mkdir()
    (bad_parent / "00.task-260527001.backlog.foo.md").write_text(
        "---\nid: '260527001'\ntype: task\nstatus: backlog\npriority: '00'\nslug: foo\n---\nbody\n"
    )
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "orphan_parent" in r.output


def test_validate_detects_orphan_special_file(tmp_path: Path) -> None:
    """A _<id>.md file at top level of a status folder."""
    (tmp_path / "backlog").mkdir()
    (tmp_path / "backlog" / "_260527001.md").write_text(
        "---\nid: '260527001'\n---\nbody\n"
    )
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "orphan_special_file" in r.output


def test_validate_detects_special_file_id_mismatch(tmp_path: Path) -> None:
    """A _<id>.md whose ID does not match its parent directory's ID."""
    (tmp_path / "backlog").mkdir()
    dir_path = tmp_path / "backlog" / "00.epic-260527000.backlog.foo"
    dir_path.mkdir()
    # Mismatched: special file says 260527999, parent says 260527000.
    (dir_path / "_260527999.md").write_text(
        "---\nid: '260527999'\n---\nbody\n"
    )
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "special_file_id_mismatch" in r.output


# ---- JSON ----


def test_validate_json_clean(tmp_path: Path) -> None:
    (tmp_path / "backlog").mkdir()
    r = runner.invoke(app, ["validate", "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["ok"] is True
    assert data["findings"] == []


def test_validate_json_with_findings(tmp_path: Path) -> None:
    (tmp_path / "backlog").mkdir()
    (tmp_path / "backlog" / "broken.md").write_text("---\n---\n")
    r = runner.invoke(app, ["validate", "--json"], env=_env(tmp_path))
    assert r.exit_code != 0
    data = json.loads(r.output)
    assert data["ok"] is False
    assert len(data["findings"]) >= 1
    codes = {f["code"] for f in data["findings"]}
    assert "bad_name_grammar" in codes
