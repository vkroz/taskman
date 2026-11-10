"""Tests for the ready, dependents, and waiting-on query commands."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage.sql_store import open_sql_store
from tests import sql_helpers as sql
from tests.workflow_helpers import prepare
from taskman.model.layout import find_item_by_id
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file, write_file

runner = CliRunner()


def _item(title: str) -> str:
    """Create a finalized item that can close; return its key."""
    key = sql.new(title)
    sql.prepare(key)
    return key


def _depend(key: str, *deps: str) -> None:
    sql.run("depend", key, *[arg for dep in deps for arg in ("--on", dep)])


def _done(key: str) -> None:
    sql.prepare(key)
    sql.run("close", key)


def _ids(*args: str) -> list[str]:
    return [row["id"] for row in json.loads(sql.run(*args, "--json"))]


# ---- ready ----


def test_ready_empty_tree(sqlite_env) -> None:
    assert "(none)" in sql.run("ready")


def test_ready_includes_items_with_no_deps(sqlite_env) -> None:
    a = _item("A")
    assert f" {a} " in sql.run("ready")


def test_ready_excludes_done_items(sqlite_env) -> None:
    a = _item("A")
    _done(a)
    assert "(none)" in sql.run("ready")


def test_ready_excludes_items_with_open_deps(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    _depend(b, a)
    # A is ready (no deps), B is not (depends on open A).
    ids = _ids("ready")
    assert a in ids
    assert b not in ids


def test_ready_includes_items_after_deps_done(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    _depend(b, a)
    _done(a)
    assert b in _ids("ready")


def test_ready_transitive_chain(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    c = _item("C")
    _depend(b, a)
    _depend(c, b)
    # Only A is ready initially.
    assert _ids("ready") == [a]


def test_ready_no_infinite_loop_on_cycle(sqlite_env) -> None:
    """Cycle in deps should not infinite-loop the query."""
    a = _item("A")
    b = _item("B")
    _depend(a, b)
    # `depend` rejects cycles; the store does not, so write the second edge directly.
    store = open_sql_store(sqlite_env, actor="pytest")
    try:
        store.save(b, {"depends_on": [a]}, store.get(b).body)
    finally:
        store.close()
    # Both have open deps; neither is ready. Should complete.
    assert _ids("ready") == []


# ---- dependents ----


def test_dependents_returns_items_referencing_target(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    c = _item("C")
    _depend(b, a)
    _depend(c, a)
    assert set(_ids("dependents", a)) == {b, c}


def test_dependents_excludes_done_items(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    _depend(b, a)
    _done(a)
    _done(b)
    assert _ids("dependents", a) == []


def test_dependents_missing_target(sqlite_env) -> None:
    assert "not found" in sql.run("dependents", "TM-99", code=1)


# ---- waiting-on ----


def test_waiting_on_returns_open_deps_of_target(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    c = _item("C")
    _depend(c, a, b)
    assert set(_ids("waiting-on", c)) == {a, b}


def test_waiting_on_excludes_done_deps(sqlite_env) -> None:
    a = _item("A")
    b = _item("B")
    c = _item("C")
    _depend(c, a, b)
    _done(a)
    assert set(_ids("waiting-on", c)) == {b}


def test_waiting_on_missing_target(sqlite_env) -> None:
    assert "not found" in sql.run("waiting-on", "TM-99", code=1)


# ---- file backend: layout only; removed with 261110001 ----
# A SQL store rejects dependencies on unknown keys, so only hand-edited
# files can hold them.


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_BACKEND": "file", "TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str) -> str:
    r = runner.invoke(app, ["new", "--title", title], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    item_id = parse_name(Path(r.output.strip()).name).item_id
    prepare(tmp_path, item_id)
    return item_id


def _set_depends(tmp_path: Path, item_id: str, deps: list[str]) -> None:
    path = find_item_by_id(tmp_path, item_id)
    body_file = (
        path / special_file_name(item_id) if path.is_dir() else path
    )
    data, body = read_file(body_file)
    data["depends_on"] = deps
    write_file(body_file, data, body)


def test_ready_excludes_items_with_unknown_deps(tmp_path: Path) -> None:
    """Unknown dep treated as blocking — the item is waiting on something that doesn't exist."""
    a = _new(tmp_path, "A")
    _set_depends(tmp_path, a, ["999999999"])
    data = json.loads(runner.invoke(app, ["ready", "--json"], env=_env(tmp_path)).output)
    ids = [d["id"] for d in data]
    assert a not in ids


def test_waiting_on_reports_unknown_dep_refs(tmp_path: Path) -> None:
    """Missing dependencies cannot be mistaken for an unblocked item."""
    a = _new(tmp_path, "A")
    _set_depends(tmp_path, a, ["999999999"])
    r = runner.invoke(app, ["waiting-on", a, "--json"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "missing dependencies" in r.output
    assert "999999999" in r.output
