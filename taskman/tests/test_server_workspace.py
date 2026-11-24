"""The workspace page of the server: rendering, local mode, its Host and header checks, and its settings."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from taskman.cli import app
from taskman.workspace import render_page
from tests import sql_helpers as sql
from tests.conftest import RunningServer

runner = CliRunner()
JSON = {"Content-Type": "application/json"}
UI = {**JSON, "X-Taskman": "1"}


def request(base: str, path: str, data: dict | None = None, headers: dict | None = None):
    """Status, content type and body text of a request; POST when ``data`` is given."""
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base + path, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.headers["Content-Type"], response.read().decode()
    except HTTPError as error:
        return error.code, error.headers["Content-Type"], error.read().decode()


@pytest.fixture
def local(sqlite_env):
    """A server in local mode; Ada is the author of the page's changes."""
    running = RunningServer(sqlite_env, local_person="Ada")
    yield running
    running.stop()


# ---- rendering ----


def test_render_page_contains_required_sections() -> None:
    html = render_page()
    assert 'id="tree" role="treegrid"' in html
    assert 'aria-label="Work views"' in html
    assert 'id="search"' in html
    assert 'id="toolbar"' in html
    assert 'id="preview"' in html
    assert 'role="status"' in html and 'role="alert"' in html
    for removed in ('id="apply-filters"', 'id="hide-done"', "Apply filters", "Plan, follow through",
                    "Shared context. Work that moves forward.", "Changes stay in your local Taskman project.", "Pri."):
        assert removed not in html
    assert 'type="checkbox"' not in html and 'id="bulk"' not in html  # rows have no selection
    assert "cdn." not in html and "<link" not in html  # works offline
    assert ".innerHTML" not in html  # components use DOM APIs and textContent


def test_render_page_holds_no_item_data(sqlite_env) -> None:
    """Item data arrives through JSON, never interpolated into the page."""
    sql.new("<script>alert(1)</script>")
    html = render_page()
    assert "<script>alert" not in html
    assert "alert(1)" not in html


# ---- `taskman serve` in local mode ----


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_serve_in_local_mode_serves_the_page_and_an_item(sqlite_env, tmp_path):
    key = sql.new("Shown item")
    port = _free_port()
    env = {**os.environ, "TASKMAN_LOCAL_MODE": "1", "TASKMAN_LOCAL_PERSON": "Ada"}
    log = tmp_path / "serve.log"
    with log.open("w") as out:
        process = subprocess.Popen([str(Path(sys.executable).with_name("taskman")), "serve", "--port", str(port)],
                                   env=env, stdout=out, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1).close()
                break
            except OSError:
                assert time.monotonic() < deadline and process.poll() is None, log.read_text()
                time.sleep(0.1)
        for path in ("/", f"/items/{key}"):
            status, content_type, text = request(base, path)
            assert (status, content_type) == (200, "text/html; charset=utf-8")
            assert 'id="preview"' in text
        status, _, text = request(base, f"/api/show/{key}")
        assert status == 200 and json.loads(text)["frontmatter"]["title"] == "Shown item"
        # Compose can map another port of the computer: the browser then sends that port.
        status, _, text = request(base, f"/api/show/{key}", headers={"Host": "127.0.0.1:9090"})
        assert status == 200 and json.loads(text)["link"] == f"http://127.0.0.1:9090/items/{key}"
        status, _, text = request(base, "/api/comment", {"id": key, "kind": "progress", "text": "From the page"},
                                  {**UI, "Host": "127.0.0.1:9090"})
        assert status == 200, text
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert "Local mode is on" in log.read_text() and "Ada is the author" in log.read_text()
    assert sql.show(key)["frontmatter"]["journal"][-1]["actor"] == "Ada"


# ---- Host and header checks ----


@pytest.mark.parametrize("host", ["example.com", "example.com:8080", "localhost.example.com", "127.0.0.1.nip.io",
                                  "127.0.0.2:8080", "0.0.0.0:8080", ""])
def test_local_mode_refuses_a_request_with_a_foreign_host(local, host):
    key = sql.new("Kept")
    before = local.store.get(key).metadata
    for path, data in (("/", None), (f"/items/{key}", None), (f"/api/show/{key}", None),
                       ("/api/comment", {"id": key, "kind": "progress", "text": "Rebound"}),
                       ("/api/assign", {"id": key, "assignee": "Mallory"})):
        status, _, text = request(local.url, path, data, {**UI, "Host": host})
        assert (status, json.loads(text)) == (403, {"error": "Only local Taskman UI requests are accepted."}), path
    assert local.store.get(key).metadata == before


def test_local_mode_refuses_a_change_without_the_header(local):
    key = sql.new("Kept")
    before = local.store.get(key).metadata
    for path, data in (("/api/comment", {"id": key, "kind": "progress", "text": "Cross-site"}),
                       ("/api/assign", {"id": key, "assignee": "Mallory"}), ("/api/items", {"title": "New"}),
                       ("/api/query", {})):
        for headers in (JSON, {**JSON, "X-Taskman": "0"}):
            status, _, text = request(local.url, path, data, headers)
            assert (status, json.loads(text)) == (403, {"error": "Only local Taskman UI requests are accepted."})
    assert local.store.get(key).metadata == before
    assert [item.id for item in local.store.items()] == [key]


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:9090", "localhost", "localhost:1", "LOCALHOST:8080"])
def test_local_mode_accepts_a_local_host_with_any_port(local, host):
    key = sql.new("Item")
    status, _, text = request(local.url, "/", headers={"Host": host})
    assert status == 200 and 'id="preview"' in text
    status, _, text = request(local.url, "/api/assign", {"id": key, "assignee": "Codex"}, {**UI, "Host": host})
    assert status == 200, text
    assert local.store.get(key).metadata["assignee"] == "Codex"


def test_the_person_of_local_mode_is_the_author_and_the_token_person_stays_the_author_of_api_calls(local):
    key = sql.new("Item")
    assert request(local.url, "/api/comment", {"id": key, "kind": "progress", "text": "From the page"}, UI)[0] == 200
    api = urllib.request.Request(local.url + "/api/v1/operations/record", headers={
        **JSON, "Authorization": f"Bearer {local.token}"}, data=json.dumps({"actor": "Codex", "arguments": {
            "item_id": key, "kind": "progress", "actor": "Codex", "text": "From an agent"}}).encode())
    urllib.request.urlopen(api, timeout=10).close()
    assert request(local.url, "/api/assign", {"id": key, "assignee": "Codex"}, UI)[0] == 200
    status, _, text = request(local.url, "/api/query", {}, UI)
    assert status == 200 and json.loads(text)["actor"] == "Ada"
    journal = local.store.get(key).metadata["journal"]
    assert [(entry["actor"], entry["text"]) for entry in journal] == [
        ("Ada", "From the page"), ("Codex for Vlad", "From an agent")]
    assert local.store.activity(key)[-1]["author"] == "Ada"


def test_local_mode_keeps_the_server_api(local):
    assert request(local.url, "/healthz")[0] == 200
    status, _, text = request(local.url, "/api/v1/items")
    assert status == 401 and json.loads(text)["error"]["kind"] == "token"
    status, _, text = request(local.url, "/api/v1/whoami", headers={"Authorization": f"Bearer {local.token}"})
    assert (status, json.loads(text)) == (200, {"person": "Vlad"})


def test_reorder_persists_and_rejects_an_invalid_position(local):
    ids = [sql.new(title) for title in ("A", "B", "C")]
    status, _, text = request(local.url, "/api/reorder", {"id": ids[2], "position": 1}, UI)
    assert (status, json.loads(text)["order"]) == (200, [ids[2], ids[0], ids[1]])
    before = local.store.sibling_order()
    assert before["root:TM"] == [ids[2], ids[0], ids[1]]
    assert request(local.url, "/api/reorder", {"id": ids[0], "position": 99}, UI)[0] == 400
    assert local.store.sibling_order() == before


# ---- without local mode ----


def test_without_local_mode_and_sign_in_the_page_says_that_login_is_not_set_up(server):
    key = sql.new("Hidden")
    before = server.store.get(key).metadata
    for path in ("/", f"/items/{key}"):
        status, content_type, text = request(server.url, path, headers={"Host": "127.0.0.1"})
        assert (status, content_type) == (200, "text/html; charset=utf-8"), path
        assert "Browser login is not set up" in text and "Hidden" not in text and 'id="preview"' not in text
        for setting in ("TASKMAN_GOOGLE_CLIENT_ID", "TASKMAN_GOOGLE_CLIENT_SECRET", "TASKMAN_PUBLIC_URL",
                        "TASKMAN_ALLOWED_EMAILS", "TASKMAN_ALLOWED_DOMAINS", "TASKMAN_LOCAL_MODE=1"):
            assert setting in text, setting
    status, _, text = request(server.url, f"/api/show/{key}", headers={"Host": "127.0.0.1"})
    assert status == 404 and json.loads(text)["error"]["kind"] == "missing"
    for path, data in (("/api/query", {}), ("/api/comment", {"id": key, "kind": "progress", "text": "No"}),
                       ("/api/items", {"title": "No", "project": "TM"})):
        status, _, text = request(server.url, path, data, {**UI, "Host": "127.0.0.1"})
        assert status == 404 and json.loads(text)["error"]["kind"] == "missing", path
    assert server.store.get(key).metadata == before
    assert [item.id for item in server.store.items()] == [key]
    assert request(server.url, "/healthz")[0] == 200


# ---- settings ----


@pytest.mark.parametrize("person", [None, "", "   ", "x" * 61])
def test_local_mode_without_a_person_name_does_not_start(sqlite_env, monkeypatch, person):
    monkeypatch.setenv("TASKMAN_LOCAL_MODE", "1")
    if person is not None:
        monkeypatch.setenv("TASKMAN_LOCAL_PERSON", person)
    result = runner.invoke(app, ["serve", "--port", "0"])
    assert result.exit_code == 2, result.output
    assert "Error: local mode needs TASKMAN_LOCAL_PERSON" in result.output
    assert "listens" not in result.output


def test_a_wrong_local_mode_value_does_not_start(sqlite_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_LOCAL_MODE", "yes")
    monkeypatch.setenv("TASKMAN_LOCAL_PERSON", "Ada")
    result = runner.invoke(app, ["serve", "--port", "0"])
    assert result.exit_code == 2, result.output
    assert "Error: TASKMAN_LOCAL_MODE must be 1 (local mode on) or 0 (off)." in result.output


# ---- the removed command ----


def test_ui_is_not_a_command():
    result = runner.invoke(app, ["ui"])
    assert result.exit_code == 2
    assert "No such command 'ui'" in result.output
    assert "ui" not in get_command(app).commands
    assert " ui " not in runner.invoke(app, ["--help"]).output
