"""UI API on the sqlite backend: keys, projects, comments, assignees, labels and activity."""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from taskman import lifecycle
from taskman.commands.ui import _make_handler
from taskman.storage.schema import upgrade
from taskman.storage.settings import StorageSettings
from taskman.storage.sql import connect
from taskman.storage.sql_store import open_sql_store


def _serve(store):
    server = ThreadingHTTPServer(("localhost", 0), _make_handler(store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://localhost:{server.server_port}"

    def call(path, data=None, status=200):
        headers = {"Content-Type": "application/json", "X-Taskman": "1"}
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(base + path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                code, value = response.status, json.load(response)
        except HTTPError as error:
            code, value = error.code, json.load(error)
        assert code == status, value
        return value

    return server, call


@pytest.fixture
def api(tmp_path):
    settings = StorageSettings("sqlite", sqlite_path=tmp_path / "taskman.db")
    conn, dialect = connect(settings)
    upgrade(conn, dialect)
    conn.close()
    # The store is opened here and used from the server threads, as `taskman ui` does.
    store = open_sql_store(settings, actor="ui-user")
    server, call = _serve(store)
    yield call
    server.shutdown()
    server.server_close()
    store.close()


def _create(api, title, **fields):
    created = api("/api/items", {"title": title, "type": "task", "body": "Body", **fields}, status=201)
    assert created.pop("row")["id"] == created["id"]
    return created


def test_create_uses_projects_and_keys(api):
    assert "needs a project key" in api("/api/items", {"title": "Orphan", "type": "task", "body": ""}, status=400)["error"]
    assert "uppercase" in api("/api/items", {"title": "Bad", "type": "task", "body": "", "project": "tm"}, status=400)["error"]
    assert _create(api, "Feature", type="feat", project="TM") == {"id": "TM-1", "path": "sqlite:TM-1"}
    assert _create(api, "Child", parent="TM-1")["id"] == "TM-2"
    assert "not found" in api("/api/items", {"title": "Lost", "type": "task", "body": "", "parent": "TM-9"}, status=400)["error"]
    shown = api("/api/show/TM-2")
    assert (shown["frontmatter"]["project"], shown["frontmatter"]["created_by"]) == ("TM", "ui-user")


def test_comments_with_kinds_and_results(api):
    _create(api, "Item", project="TM")
    api("/api/comment", {"id": "TM-1", "kind": "decision", "text": "Chose SQLite"})
    api("/api/comment", {"id": "TM-1", "kind": "verification", "result": "pass", "text": "Checked"})
    assert "result" in api("/api/comment", {"id": "TM-1", "kind": "verification", "text": "x"}, status=400)["error"]
    assert "result" in api("/api/comment", {"id": "TM-1", "kind": "progress", "result": "pass", "text": "x"}, status=400)["error"]
    assert "text" in api("/api/comment", {"id": "TM-1", "kind": "decision", "text": " "}, status=400)["error"]
    assert "kind" in api("/api/comment", {"id": "TM-1", "kind": "note", "text": "x"}, status=400)["error"]
    journal = api("/api/show/TM-1")["frontmatter"]["journal"]
    assert [(e["kind"], e["actor"], e["text"], e.get("result")) for e in journal] == [
        ("decision", "ui-user", "Chose SQLite", None),
        ("verification", "ui-user", "Checked", "pass"),
    ]


def test_assignee_and_labels(api):
    _create(api, "Item", project="TM")
    assigned = api("/api/assign", {"id": "TM-1", "assignee": "codex"})
    assert (assigned["ok"], assigned["row"]["assignee"]) == (True, "codex")
    assert "already" in api("/api/assign", {"id": "TM-1", "assignee": "codex"})["message"]
    assert "1-100" in api("/api/assign", {"id": "TM-1", "assignee": "x" * 101}, status=400)["error"]
    api("/api/label", {"id": "TM-1", "add": ["ui", "db"], "remove": []})
    api("/api/label", {"id": "TM-1", "add": [], "remove": ["ui"]})
    assert "label" in api("/api/label", {"id": "TM-1", "add": ["Bad Label"]}, status=400)["error"]
    assert "lists" in api("/api/label", {"id": "TM-1", "add": "db"}, status=400)["error"]
    meta = api("/api/show/TM-1")["frontmatter"]
    assert (meta["assignee"], meta["labels"]) == ("codex", ["db"])
    api("/api/assign", {"id": "TM-1", "assignee": None})
    assert api("/api/show/TM-1")["frontmatter"]["assignee"] is None
    rows = api("/api/query", {"filters": [{"field": "labels", "op": "eq", "value": "db"}]})["rows"]
    assert [row["id"] for row in rows] == ["TM-1"]


def test_show_includes_activity(api):
    _create(api, "Item", project="TM")
    api("/api/assign", {"id": "TM-1", "assignee": "codex"})
    api("/api/edit", {"id": "TM-1", "title": "Renamed", "body": "Body", "reason": "Clarify"})
    activity = api("/api/show/TM-1")["activity"]
    assert [(a["field"], a["author"]) for a in activity] == [("created", "ui-user"), ("assignee", "ui-user"), ("title", "ui-user")]
    assert (activity[2]["old"], activity[2]["new"]) == ("Item", "Renamed")


def test_done_items_cannot_be_reordered_or_changed(api, tmp_path):
    _create(api, "Done", project="TM")
    _create(api, "Open", project="TM")
    assert "capabilities" not in api("/api/query", {})
    store = open_sql_store(StorageSettings("sqlite", sqlite_path=tmp_path / "taskman.db"), actor="agent")
    try:
        lifecycle.finalize(store, "TM-1")
        lifecycle.record(store, "TM-1", "summary", "agent", "Done")
        lifecycle.record(store, "TM-1", "verification", "agent", "Checked", "pass")
        lifecycle.close(store, "TM-1")
    finally:
        store.close()
    assert "immutable" in api("/api/reorder", {"id": "TM-1", "position": 2}, status=400)["error"]
    assert "immutable" in api("/api/comment", {"id": "TM-1", "kind": "decision", "text": "late"}, status=400)["error"]
    assert "immutable" in api("/api/assign", {"id": "TM-1", "assignee": "codex"}, status=400)["error"]
    assert "Unknown endpoint" in api("/api/consolidate", {"ids": ["TM-1"], "title": "Summary"}, status=404)["error"]
    api("/api/reorder", {"id": "TM-2", "position": 1})
    assert [row["id"] for row in api("/api/query", {})["rows"]] == ["TM-2", "TM-1"]
