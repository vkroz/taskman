"""Integration tests for validate's depends_on graph checks."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests import sql_helpers as sql
from taskman.model.names import parse_name

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_BACKEND": "file", "TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str) -> str:
    r = runner.invoke(app, ["new", "--title", title], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    return parse_name(Path(r.output.strip()).name).item_id


def _add_dependency(settings, item: str, depends_on: str) -> None:
    """Insert a dependency row directly; the lifecycle refuses cycles."""
    conn = sqlite3.connect(settings.sqlite_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO dependencies (item_id, depends_on) "
                "SELECT i.id, d.id FROM items i, items d "
                "WHERE i.project || '-' || i.number = ? AND d.project || '-' || d.number = ?",
                (item, depends_on),
            )
    finally:
        conn.close()


def _set_depends_on(tmp_path: Path, item_id: str, deps: list[str] | object) -> None:
    """Mutate an item's frontmatter to add a depends_on list (or non-list value)."""
    from taskman.model.layout import find_item_by_id
    from taskman.model.names import special_file_name
    from taskman.model.yaml_io import read_file, write_file

    path = find_item_by_id(tmp_path, item_id)
    assert path is not None
    body_file = path / special_file_name(item_id) if path.is_dir() else path
    data, body = read_file(body_file)
    data["depends_on"] = deps
    write_file(body_file, data, body)


def test_validate_clean_with_no_depends(sqlite_env) -> None:
    sql.new("A")
    assert "OK" in sql.run("validate")


def test_validate_clean_with_valid_depends(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    sql.run("depend", b, "--on", a)
    assert "OK" in sql.run("validate")


def test_validate_simple_cycle_errors(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    sql.run("depend", a, "--on", b)
    _add_dependency(sqlite_env, b, a)
    assert "dependency_cycle" in sql.run("validate", code=1)


def test_validate_transitive_cycle_errors(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    c = sql.new("C")
    sql.run("depend", a, "--on", b)
    sql.run("depend", b, "--on", c)
    _add_dependency(sqlite_env, c, a)
    assert "dependency_cycle" in sql.run("validate", code=1)


def test_validate_json_includes_graph_findings(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    sql.run("depend", a, "--on", b)
    _add_dependency(sqlite_env, b, a)
    data = json.loads(sql.run("validate", "--json", code=1))
    assert data["ok"] is False
    codes = {f["code"] for f in data["findings"]}
    assert "dependency_cycle" in codes


def test_validate_handles_multi_edge_graph(sqlite_env) -> None:
    """Item with multiple deps, all valid → no findings."""
    a = sql.new("A")
    b = sql.new("B")
    c = sql.new("C")
    sql.run("depend", c, "--on", a, "--on", b)
    assert sql.show(c)["frontmatter"]["depends_on"] == [a, b]
    assert "OK" in sql.run("validate")


# ---- file backend only; removed with 261110001 ----
# SQLite cannot store these: a foreign key ties every dependency to an existing
# item, and a dependency table holds no non-list value.


def test_validate_unknown_dependency_warns(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    _set_depends_on(tmp_path, a, ["999999999"])
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    # warning does not block exit 0
    assert r.exit_code == 0
    assert "unknown_dependency" in r.output
    assert "999999999" in r.output


def test_validate_malformed_depends_on_warns(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    _set_depends_on(tmp_path, a, "not_a_list")
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    # warning only, not error
    assert r.exit_code == 0
    assert "malformed_depends_on" in r.output
