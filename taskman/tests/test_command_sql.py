"""CLI commands on the sqlite backend: keys, projects, comments, assignees, labels and activity."""
from __future__ import annotations

import json
import os
import re
import time

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage.sql_store import default_actor

runner = CliRunner()


def _env(monkeypatch, tmp_path, name="taskman.db") -> None:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("TASKMAN_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("TASKMAN_BACKEND", "sqlite")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{tmp_path / name}")


def run(*args, input=None, code=0):
    result = runner.invoke(app, [str(a) for a in args], input=input)
    assert result.exit_code == code, result.output
    # Typer colors its usage errors when GITHUB_ACTIONS or FORCE_COLOR is set.
    return re.sub(r"\x1b\[[0-9;]*m", "", result.output)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    run("db", "upgrade")
    return run


def _json(*args):
    return json.loads(run(*args))


def _comment(item, kind, text="text", result=None, actor="pytest"):
    args = ["comment", item, "--kind", kind, "--body-file", "-", "--actor", actor]
    run(*args, *(["--result", result] if result else []), input=text)


# ---- settings and connections ----------------------------------------------


def test_commands_need_the_schema(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    assert "taskman db upgrade" in run("list", code=2)
    run("db", "upgrade")
    assert run("list").strip() == "(no items)"


def test_each_command_uses_the_configured_database(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, "first.db")
    run("db", "upgrade")
    run("new", "--title", "In first", "--project", "TM")
    _env(monkeypatch, tmp_path, "second.db")
    run("db", "upgrade")
    assert run("list").strip() == "(no items)"


def test_a_write_does_not_wait_for_its_own_lock(cli):
    started = time.monotonic()
    cli("new", "--title", "Quick", "--project", "TM")
    cli("finalize", "TM-1")
    assert time.monotonic() - started < 5  # a second connection would wait 30 s for the write lock


# ---- keys and projects -----------------------------------------------------


def test_new_prints_keys_and_needs_a_project(cli):
    assert "needs a project key" in cli("new", "--title", "Orphan", code=1)
    assert "uppercase" in cli("new", "--title", "Bad", "--project", "tm", code=2)
    assert _json("new", "--title", "Epic", "--type", "epic", "--project", "TM", "--json") == {"id": "TM-1", "path": "sqlite:TM-1"}
    assert cli("new", "--title", "Child", "--parent", "TM-1").strip() == "sqlite:TM-2"
    assert _json("show", "TM-2", "--json")["frontmatter"]["project"] == "TM"
    assert "item not found" in cli("show", "TM-9", code=1)


def test_listings_filter_by_project(cli):
    for project in ("TM", "OPS", "TM"):
        cli("new", "--title", f"{project} item", "--project", project)
    for key in ("TM-1", "OPS-1", "TM-2"):
        cli("finalize", key)
    assert [row["id"] for row in _json("list", "--json", "--project", "OPS")] == ["OPS-1"]
    assert [row["id"] for row in _json("ready", "--json", "--project", "TM")] == ["TM-1", "TM-2"]
    assert [row["id"] for row in _json("search", "item", "--json", "--project", "OPS")] == ["OPS-1"]
    assert [row["id"] for row in _json("browse", "--project", "TM")] == ["TM-1", "TM-2"]
    assert len(_json("list", "--json")) == 3


def test_keys_sort_by_number(cli):
    for n in range(11):
        cli("new", "--title", f"Item {n}", "--project", "TM")
    expected = [f"TM-{n}" for n in range(1, 12)]
    assert [row["id"] for row in _json("list", "--json")] == expected
    assert [row["id"] for row in _json("browse")] == expected


# ---- comments --------------------------------------------------------------


def test_comment_record_alias_and_default_author(cli):
    cli("new", "--title", "Item", "--project", "TM")
    _comment("TM-1", "decision", "Chose SQLite", actor="alice")
    cli("record", "TM-1", "--kind", "progress", "--body-file", "-", input="Half done")
    _comment("TM-1", "verification", "Tests pass", result="pass")
    assert "result" in cli("comment", "TM-1", "--kind", "verification", "--body-file", "-", input="x", code=2)
    assert "result" in cli("comment", "TM-1", "--kind", "decision", "--result", "pass", "--body-file", "-", input="x", code=2)
    journal = _json("show", "TM-1", "--json")["frontmatter"]["journal"]
    assert [(e["kind"], e["actor"], e["text"], e.get("result")) for e in journal] == [
        ("decision", "alice", "Chose SQLite", None),
        ("progress", default_actor(), "Half done", None),
        ("verification", "pytest", "Tests pass", "pass"),
    ]
    assert [e["revision"] for e in journal] == [1, 2, 2]


# ---- assignee and labels ---------------------------------------------------


def test_assign_unassign_and_filter(cli):
    cli("new", "--title", "Mine", "--project", "TM")
    cli("new", "--title", "Other", "--project", "TM")
    assert cli("assign", "TM-1", " codex ").strip() == "Assigned TM-1 to codex"
    assert "already has this assignee" in cli("assign", "TM-1", "codex")
    assert [row["id"] for row in _json("list", "--json", "--assignee", "codex")] == ["TM-1"]
    assert [row["id"] for row in _json("browse", "--where", "assignee=codex")] == ["TM-1"]
    cli("unassign", "TM-1")
    assert _json("show", "TM-1", "--json")["frontmatter"]["assignee"] is None
    assert "1-100" in cli("assign", "TM-1", "x" * 101, code=2)
    assert "item not found" in cli("assign", "TM-9", "codex", code=2)


def test_label_add_remove_and_filter(cli):
    cli("new", "--title", "Item", "--project", "TM")
    cli("new", "--title", "Other", "--project", "TM")
    assert cli("label", "TM-1", "--add", "ui", "--add", "db").strip() == "Labels of TM-1: db, ui"
    assert cli("label", "TM-1", "--remove", "ui").strip() == "Labels of TM-1: db"
    assert "unchanged" in cli("label", "TM-1", "--add", "db")
    assert "--add or --remove" in cli("label", "TM-1", code=2)
    assert "label" in cli("label", "TM-1", "--add", "Bad Label", code=2)
    assert [row["id"] for row in _json("list", "--json", "--label", "db")] == ["TM-1"]
    assert [row["id"] for row in _json("browse", "--where", "labels=db")] == ["TM-1"]


# ---- activity and context --------------------------------------------------


def test_activity_records_each_change_with_its_author(cli):
    cli("new", "--title", "Item", "--project", "TM", "--actor", "alice")
    cli("assign", "TM-1", "codex", "--actor", "bob")
    cli("label", "TM-1", "--add", "db")
    _comment("TM-1", "decision")
    entries = _json("activity", "TM-1", "--json")
    assert [(e["field"], e["author"]) for e in entries] == [
        ("created", "alice"), ("assignee", "bob"), ("labels", default_actor()),
    ]
    assert (entries[1]["old"], entries[1]["new"]) == (None, "codex")
    text = cli("activity", "TM-1")
    assert "bob  assignee: - -> codex" in text
    assert _json("context", "TM-1")["activity"] == entries
    assert "item not found" in cli("activity", "TM-9", code=1)


# ---- lifecycle -------------------------------------------------------------


def test_close_and_convert_name_children_by_key(cli):
    cli("new", "--title", "Feature", "--type", "feat", "--project", "TM")
    cli("new", "--title", "Child", "--parent", "TM-1")
    assert "has children: TM-2" in cli("convert", "TM-1", "task", code=1)
    output = cli("close", "TM-1", code=1)
    assert "  TM-2" in output and "sqlite:" not in output
    cli("finalize", "TM-1")
    cli("finalize", "TM-2")
    for key in ("TM-2", "TM-1"):
        _comment(key, "summary", "Done")
        _comment(key, "verification", "Checked", result="pass")
        assert cli("close", key).strip() == f"sqlite:{key}"
    assert "immutable" in cli("assign", "TM-2", "codex", code=2)
    assert [e["field"] for e in _json("activity", "TM-1", "--json")][-1] == "status"


def test_command_help_needs_no_database(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    for command in ("depend", "label", "list", "new", "ui"):
        assert "Usage" in run(command, "--help")
    assert not (tmp_path / "taskman.db").exists()


def test_a_missing_database_is_reported_and_not_created(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    output = run("show", "TM-1", code=2)
    assert "There is no Taskman database" in output and "taskman db upgrade" in output
    assert not (tmp_path / "taskman.db").exists()
