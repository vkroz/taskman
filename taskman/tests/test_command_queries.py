"""Tests for the ready, dependents, and waiting-on query commands."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests.workflow_helpers import prepare
from taskman.model.layout import find_item_by_id
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file, write_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


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


def _close(tmp_path: Path, item_id: str) -> None:
    prepare(tmp_path, item_id)
    r = runner.invoke(app, ["close", item_id], env=_env(tmp_path))
    assert r.exit_code == 0, r.output


# ---- ready ----


def test_ready_empty_tree(tmp_path: Path) -> None:
    r = runner.invoke(app, ["ready"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "(none)" in r.output


def test_ready_includes_items_with_no_deps(tmp_path: Path) -> None:
    _new(tmp_path, "A")
    r = runner.invoke(app, ["ready"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "a " in r.output  # slug "a" appears


def test_ready_excludes_done_items(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    _close(tmp_path, a)
    r = runner.invoke(app, ["ready"], env=_env(tmp_path))
    assert r.exit_code == 0
    assert "(none)" in r.output


def test_ready_excludes_items_with_open_deps(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends(tmp_path, b, [a])
    r = runner.invoke(app, ["ready"], env=_env(tmp_path))
    assert r.exit_code == 0
    # A is ready (no deps), B is not (depends on open A).
    data = json.loads(runner.invoke(app, ["ready", "--json"], env=_env(tmp_path)).output)
    ids = [d["id"] for d in data]
    assert a in ids
    assert b not in ids


def test_ready_includes_items_after_deps_done(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends(tmp_path, b, [a])
    _close(tmp_path, a)
    data = json.loads(runner.invoke(app, ["ready", "--json"], env=_env(tmp_path)).output)
    ids = [d["id"] for d in data]
    assert b in ids


def test_ready_excludes_items_with_unknown_deps(tmp_path: Path) -> None:
    """Unknown dep treated as blocking — the item is waiting on something that doesn't exist."""
    a = _new(tmp_path, "A")
    _set_depends(tmp_path, a, ["999999999"])
    data = json.loads(runner.invoke(app, ["ready", "--json"], env=_env(tmp_path)).output)
    ids = [d["id"] for d in data]
    assert a not in ids


def test_ready_transitive_chain(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    c = _new(tmp_path, "C")
    _set_depends(tmp_path, b, [a])
    _set_depends(tmp_path, c, [b])
    # Only A is ready initially.
    data = json.loads(runner.invoke(app, ["ready", "--json"], env=_env(tmp_path)).output)
    ids = [d["id"] for d in data]
    assert ids == [a]


def test_ready_no_infinite_loop_on_cycle(tmp_path: Path) -> None:
    """Cycle in deps should not infinite-loop the query."""
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends(tmp_path, a, [b])
    _set_depends(tmp_path, b, [a])
    # Both have open deps; neither is ready. Should complete.
    r = runner.invoke(app, ["ready", "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data == []


# ---- dependents ----


def test_dependents_returns_items_referencing_target(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    c = _new(tmp_path, "C")
    _set_depends(tmp_path, b, [a])
    _set_depends(tmp_path, c, [a])
    r = runner.invoke(app, ["dependents", a, "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    ids = {d["id"] for d in json.loads(r.output)}
    assert ids == {b, c}


def test_dependents_excludes_done_items(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends(tmp_path, b, [a])
    _close(tmp_path, a)
    _close(tmp_path, b)
    data = json.loads(runner.invoke(app, ["dependents", a, "--json"], env=_env(tmp_path)).output)
    assert data == []


def test_dependents_missing_target(tmp_path: Path) -> None:
    r = runner.invoke(app, ["dependents", "999999999"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "not found" in r.output


# ---- waiting-on ----


def test_waiting_on_returns_open_deps_of_target(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    c = _new(tmp_path, "C")
    _set_depends(tmp_path, c, [a, b])
    r = runner.invoke(app, ["waiting-on", c, "--json"], env=_env(tmp_path))
    assert r.exit_code == 0
    ids = {d["id"] for d in json.loads(r.output)}
    assert ids == {a, b}


def test_waiting_on_excludes_done_deps(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    c = _new(tmp_path, "C")
    _set_depends(tmp_path, c, [a, b])
    _close(tmp_path, a)
    ids = {d["id"] for d in json.loads(runner.invoke(app, ["waiting-on", c, "--json"], env=_env(tmp_path)).output)}
    assert ids == {b}


def test_waiting_on_missing_target(tmp_path: Path) -> None:
    r = runner.invoke(app, ["waiting-on", "999999999"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "not found" in r.output


def test_waiting_on_reports_unknown_dep_refs(tmp_path: Path) -> None:
    """Missing dependencies cannot be mistaken for an unblocked item."""
    a = _new(tmp_path, "A")
    _set_depends(tmp_path, a, ["999999999"])
    r = runner.invoke(app, ["waiting-on", a, "--json"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "missing dependencies" in r.output
    assert "999999999" in r.output
