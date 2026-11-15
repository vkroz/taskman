"""Tests for the ready, dependents, and waiting-on query commands."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage.sql_store import open_sql_store
from tests import sql_helpers as sql

runner = CliRunner()


def _item(title: str) -> str:
    """Create a finalized item that can close; return its key."""
    key = sql.new(title)
    sql.prepare(key)
    return key


def _item_under(title: str, parent: str) -> str:
    """Create a finalized child item; return its key."""
    key = sql.new(title, parent=parent)
    sql.run("finalize", key)
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


def _ready(*args: str):
    result = runner.invoke(app, ["ready", *args])
    assert result.exit_code == 0, result.output
    return result


def test_ready_names_the_open_items_it_leaves_out_on_stderr(sqlite_env) -> None:
    feat = sql.new("Feature", "feat")
    sql.prepare(feat)
    first = _item_under("First", feat)
    blocked = _item_under("Blocked", feat)
    _depend(blocked, first)
    draft = sql.new("Draft leaf", parent=feat)
    draft_feat = sql.new("Draft feature", "feat")
    under_draft = _item_under("Under draft", draft_feat)
    result = _ready()
    assert [line.split()[2] for line in result.stdout.splitlines()] == [feat, first]
    assert result.stderr == (
        f"Not ready: {draft}, {draft_feat} (draft); {under_draft} (under a draft); "
        f"{blocked} (waiting on dependencies). To see them, run: taskman list\n"
    )
    json_result = _ready("--json")
    assert [row["id"] for row in json.loads(json_result.stdout)] == [feat, first]
    assert json_result.stderr == ""


def test_ready_names_at_most_ten_left_out_items(sqlite_env) -> None:
    keys = [sql.new(f"Draft {n}") for n in range(12)]
    result = _ready()
    assert result.stdout == "(none)\n"
    assert result.stderr == (
        f"Not ready: {', '.join(keys[:10])} (draft); and 2 more. To see them, run: taskman list\n"
    )


def test_ready_prints_no_note_when_it_leaves_nothing_out(sqlite_env) -> None:
    a = _item("A")
    _done(_item("Closed"))
    other = sql.new("Other project draft", project="OPS")
    result = _ready("--project", "TM")
    assert f" {a} " in result.stdout and result.stderr == ""
    assert other in _ready().stderr


def test_ready_help_says_what_ready_means(sqlite_env) -> None:
    text = " ".join(runner.invoke(app, ["ready", "--help"]).output.split())
    assert "the item and all its ancestors are finalized, not done, and have no unfinished dependency" in text


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
