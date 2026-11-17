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


def test_edit_changes_only_the_title_without_a_body_file(cli):
    cli("new", "--title", "Item", "--project", "TM", "--body-file", "-", input="Item spec.\n")
    cli("new", "--title", "Draft", "--project", "TM", "--body-file", "-", input="Draft spec.\n")
    cli("finalize", "TM-1")
    for key, body in (("TM-1", "Item spec.\n"), ("TM-2", "Draft spec.\n")):
        assert cli("edit", key, "--title", "Renamed", "--reason", "Rename only") == f"Updated: {key}\n"
        assert (_json("show", key, "--json")["frontmatter"]["title"], _json("show", key, "--json")["body"]) == (
            "Renamed", body)
    assert "Description must not be empty" in cli("edit", "TM-1", "--title", "T", "--reason", "Clear",
                                                  "--body-file", "-", input="", code=2)
    assert _json("show", "TM-1", "--json")["body"] == "Item spec.\n"


def test_edit_on_the_command_line_needs_a_reason(cli):
    cli("new", "--title", "Item", "--project", "TM")
    assert "Explain why the task changed" in cli("edit", "TM-1", "--title", "Renamed", "--reason", " ", code=2)
    assert _json("show", "TM-1", "--json")["frontmatter"]["title"] == "Item"


# ---- keys and projects -----------------------------------------------------


def test_new_prints_keys_and_needs_a_project(cli):
    assert "needs a project key" in cli("new", "--title", "Orphan", code=1)
    assert "uppercase" in cli("new", "--title", "Bad", "--project", "tm", code=2)
    assert _json("new", "--title", "Epic", "--type", "epic", "--project", "TM", "--json") == {"id": "TM-1", "path": "sqlite:TM-1"}
    assert cli("new", "--title", "Child", "--parent", "TM-1") == "TM-2\n"
    assert cli("update", "TM-2", "--body-file", "-", input="Spec.\n") == "TM-2\n"
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


def test_comment_takes_the_text_inline_or_from_a_file(cli, tmp_path):
    cli("new", "--title", "Item", "--project", "TM")
    assert cli("comment", "TM-1", "--kind", "progress", "--body", "Short note").strip() == "Recorded progress: TM-1"
    cli("record", "TM-1", "--kind", "decision", "--body", "Inline alias", "--actor", "alice")
    (tmp_path / "note.md").write_text("From a file", encoding="utf-8")
    cli("comment", "TM-1", "--kind", "progress", "--body-file", tmp_path / "note.md")
    journal = _json("show", "TM-1", "--json")["frontmatter"]["journal"]
    assert [(e["kind"], e["actor"], e["text"]) for e in journal] == [
        ("progress", default_actor(), "Short note"),
        ("decision", "alice", "Inline alias"),
        ("progress", default_actor(), "From a file"),
    ]


@pytest.mark.parametrize("command", ["comment", "record"])
@pytest.mark.parametrize("text", [[], ["--body", "x", "--body-file", "-"]], ids=["neither", "both"])
def test_comment_needs_exactly_one_text_option(cli, command, text):
    cli("new", "--title", "Item", "--project", "TM")
    output = cli(command, "TM-1", "--kind", "progress", *text, input="y", code=2)
    assert "exactly one of --body and --body-file" in output
    assert _json("show", "TM-1", "--json")["frontmatter"]["journal"] == []


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
    assert "item not found" in cli("assign", "TM-9", "codex", code=1)


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


def test_repo_sets_shows_inherits_and_clears(cli):
    cli("new", "--type", "feat", "--title", "Feat", "--project", "TM")
    cli("new", "--title", "Task", "--parent", "TM-1")
    assert cli("repo", "TM-1").strip() == "Repositories of TM-1: none"
    out = cli("repo", "TM-1", "git@github.com:vkroz/taskman.git", "https://github.com/vkroz/other")
    assert out.strip() == "Repositories of TM-1: github.com/vkroz/other, github.com/vkroz/taskman"
    assert cli("repo", "TM-2").strip() == (
        "Repositories of TM-2, inherited from TM-1: github.com/vkroz/other, github.com/vkroz/taskman"
    )
    assert "unchanged" in cli("repo", "TM-1", "github.com/vkroz/taskman", "github.com/vkroz/other")
    assert "remote address" in cli("repo", "TM-1", "/Users/me/taskman", code=2)
    assert "not both" in cli("repo", "TM-1", "github.com/o/r", "--clear", code=2)
    assert "item not found" in cli("repo", "TM-9", "github.com/o/r", code=1)

    inherited = {"from": "TM-1", "addresses": ["github.com/vkroz/other", "github.com/vkroz/taskman"]}
    shown = _json("show", "TM-2", "--json")
    assert (shown["frontmatter"]["repositories"], shown["inherited_repositories"]) == ([], inherited)
    assert "Repositories inherited from TM-1: github.com/vkroz/other, github.com/vkroz/taskman" in cli("show", "TM-2")
    context = _json("context", "TM-2")["item"]
    assert (context["frontmatter"]["repositories"], context["inherited_repositories"]) == ([], inherited)
    rows = {row["id"]: row for row in _json("list", "--json")}
    assert (rows["TM-1"]["repositories"], rows["TM-1"]["inherited_repositories"]) == (inherited["addresses"], None)
    assert (rows["TM-2"]["repositories"], rows["TM-2"]["inherited_repositories"]) == ([], inherited)

    assert cli("repo", "TM-1", "--clear").strip() == "Repositories of TM-1: none"
    assert _json("show", "TM-2", "--json")["inherited_repositories"] is None
    assert [e["field"] for e in _json("activity", "TM-1", "--json")].count("repositories") == 2


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
        assert cli("close", key) == f"{key}\n"
    assert "immutable" in cli("assign", "TM-2", "codex", code=2)
    assert [e["field"] for e in _json("activity", "TM-1", "--json")][-1] == "status"


def test_command_help_needs_no_database(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    for command in ("depend", "label", "list", "new", "ui"):
        assert "Usage" in run(command, "--help")
    assert not (tmp_path / "taskman.db").exists()


def test_a_missing_database_is_reported_and_not_created(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    for args in (("show", "TM-1"), ("list",), ("ready",), ("new", "--title", "T", "--project", "TM")):
        output = run(*args, code=2)
        assert f"No Taskman server is set, and there is no local database at {tmp_path / 'taskman.db'}." in output, args
        assert "run `taskman auth login --server URL`, or set TASKMAN_SERVER_URL and TASKMAN_TOKEN" in output, args
        assert "To create a local database, run `taskman db upgrade`." in output, args
    assert not (tmp_path / "taskman.db").exists()
    # The server's own commands cannot use a server, so they name only the database.
    output = run("token", "list", code=2)
    assert "There is no Taskman database" in output and "auth login" not in output
    run("db", "upgrade")
    assert run("list").strip() == "(no items)"
