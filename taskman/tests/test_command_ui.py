"""Tests for the ui command — data prep + server smoke."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from typer.testing import CliRunner

from taskman.cli import app
from tests import sql_helpers as sql
from taskman.commands.ui import _make_handler, render_page
from taskman.storage.sql_store import open_sql_store

runner = CliRunner()


@pytest.fixture
def store(sqlite_env):
    store = open_sql_store(sqlite_env, actor="pytest")
    yield store
    store.close()


def _new(title: str, item_type: str = "task") -> str:
    key = sql.new(title, item_type)
    sql.prepare(key)
    return key


# ---- rendering ----


def test_render_page_contains_required_sections(store) -> None:
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


def test_render_page_holds_no_item_data(store) -> None:
    """Item data arrives through JSON, never interpolated into the page."""
    _new("<script>alert(1)</script>")
    html = render_page()
    assert "<script>alert" not in html
    assert "alert(1)" not in html


# ---- server smoke ----


def test_server_smoke_serves_html(store) -> None:
    """Start the server on an ephemeral port, fetch /, assert 200 + section markers."""
    _new("Smoke Test Item")
    handler = _make_handler(store)
    server = ThreadingHTTPServer(("localhost", 0), handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)  # let the server be ready
        with urllib.request.urlopen(f"http://localhost:{port}/", timeout=3) as r:
            assert r.status == 200
            body = r.read().decode("utf-8")
        assert 'id="tree"' in body
        assert 'aria-label="Work views"' in body
        assert 'id="preview"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_server_api_show_returns_json(store) -> None:
    """The /api/show/<id> endpoint returns JSON with frontmatter + body."""
    item_id = _new("Api Test")
    handler = _make_handler(store)
    server = ThreadingHTTPServer(("localhost", 0), handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        with urllib.request.urlopen(f"http://localhost:{port}/api/show/{item_id}", timeout=3) as r:
            assert r.status == 200
            data = json.loads(r.read())
        assert "frontmatter" in data
        assert "body" in data
        assert data["frontmatter"]["title"] == "Api Test"
    finally:
        server.shutdown()
        server.server_close()


def test_server_api_show_missing_returns_404(store) -> None:
    handler = _make_handler(store)
    server = ThreadingHTTPServer(("localhost", 0), handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/show/TM-99", timeout=3)
            raised = False
        except urllib.error.HTTPError as e:
            assert e.code == 404
            raised = True
        assert raised
    finally:
        server.shutdown()
        server.server_close()


def test_server_api_reorder_persists_and_rejects_invalid_position(store) -> None:
    from urllib.error import HTTPError

    ids = [_new(title) for title in ("A", "B", "C")]
    handler = _make_handler(store)
    server = ThreadingHTTPServer(("localhost", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(data):
        request = urllib.request.Request(
            f"http://localhost:{server.server_port}/api/reorder",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Taskman": "1"},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    try:
        assert post({"id": ids[2], "position": 1})["order"] == [ids[2], ids[0], ids[1]]
        before = store.sibling_order()
        assert before["root:TM"] == [ids[2], ids[0], ids[1]]
        with pytest.raises(HTTPError) as error:
            post({"id": ids[0], "position": 99})
        assert error.value.code == 400
        assert store.sibling_order() == before
    finally:
        server.shutdown()
        server.server_close()


# ---- --stop ----


def test_ui_stop_when_no_server_running(sqlite_env, tmp_path: Path, monkeypatch) -> None:
    """--stop with no running server reports cleanly, exits 0."""
    monkeypatch.setattr("taskman.commands.ui._pid_file", lambda: tmp_path / "ui.pid")
    r = runner.invoke(app, ["ui", "--stop"])
    assert r.exit_code == 0
