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
from taskman.commands.ui import (
    _build_mermaid,
    _Item,
    _make_handler,
    collect_page,
    render_page,
)
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


# ---- data aggregation ----


def test_collect_page_empty_tree(store) -> None:
    page = collect_page(store)
    assert page.items == []
    assert page.ready == []
    assert page.blocked == []
    assert page.recent == []


def test_collect_page_ready_and_blocked(store) -> None:
    a = _new("A")
    b = _new("B")
    sql.run("depend", b, "--on", a)
    page = collect_page(store)
    ready_ids = {i.item_id for i in page.ready}
    blocked_ids = {i.item_id for i in page.blocked}
    assert a in ready_ids
    assert b in blocked_ids
    assert f"{a} --> {b}" in page.graph_mermaid


def test_collect_page_includes_done_in_items_but_not_ready(store) -> None:
    a = _new("A")
    sql.run("close", a)
    page = collect_page(store)
    assert any(i.item_id == a for i in page.items)
    assert not any(i.item_id == a for i in page.ready)
    assert not any(i.item_id == a for i in page.blocked)
    assert [i.item_id for i in page.recent] == [a]


def test_build_mermaid_empty() -> None:
    out = _build_mermaid([])
    assert out.startswith("flowchart")
    assert "empty" in out


def test_build_mermaid_with_edges() -> None:
    items = [
        _Item("260527000", "epic", "backlog", "00", "a", "A", "/p/a", (), 0),
        _Item("260527001", "task", "backlog", "00", "b", "B", "/p/b", ("260527000",), 0),
    ]
    out = _build_mermaid(items)
    assert "260527000" in out
    assert "260527001" in out
    assert "260527000 --> 260527001" in out


# ---- rendering ----


def test_render_page_contains_required_sections(store) -> None:
    _new("X")
    page = collect_page(store)
    html = render_page(page)
    assert 'id="tree"' in html
    assert 'aria-label="Work views"' in html
    assert 'id="filters"' in html
    assert 'id="filter-form"' in html
    assert 'id="apply-filters"' in html
    assert 'id="hide-done"' in html
    assert 'aria-pressed="false"' in html
    assert 'type="button" aria-label="Remove filter' in html
    assert 'id="direction"' not in html
    assert 'id="editor"' in html
    assert 'id="preview"' in html
    assert 'Consolidated history' in html
    assert 'cdn.jsdelivr' not in html


def test_render_page_escapes_html(store) -> None:
    """Item titles with HTML characters are escaped."""
    _new("<script>alert(1)</script>")
    page = collect_page(store)
    html = render_page(page)
    assert "<script>alert" not in html
    assert "alert(1)" not in html  # Data arrives through JSON, never interpolated into script.


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
        assert before["root"] == [ids[2], ids[0], ids[1]]
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


def test_checkbox_selection_follows_hierarchy_and_deselects_individually():
    """Execute the shipped event handler with hidden descendants and two branches."""
    import shutil
    import subprocess

    import pytest
    import taskman.commands.ui as ui

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the browser event handler")
    source = Path(ui.__file__).with_name("workspace.html").read_text()
    handler = source.split('$("#tree").onchange = (e) => {', 1)[1].split(
        '\n      };', 1
    )[0]
    script = r'''
const assert = require("node:assert/strict");
const all = [
  {id: "root", parent_id: null},
  {id: "child", parent_id: "root"},
  {id: "grandchild", parent_id: "child"},
  {id: "sibling", parent_id: "root"},
  {id: "other", parent_id: null},
  {id: "other-child", parent_id: "other"},
  {id: "archived", parent_id: "root", archived: true},
];
// Only root is visible: selection must use all, not rendered rows.
const rows = [all[0]], collapsed = new Set(["root"]);
const selected = new Set(["other-child"]);
let renders = 0;
function render() { renders++; }
const change = (e) => { HANDLER };
function click(id, checked) { change({target: {dataset: {select: id}, checked}}); }
function expect(ids) { assert.deepEqual([...selected].sort(), ids.sort()); }
click("root", true);
expect(["root", "child", "grandchild", "sibling", "other-child"]);
click("root", false);
expect(["child", "grandchild", "sibling", "other-child"]);
click("child", false);
expect(["grandchild", "sibling", "other-child"]);
click("root", true);
click("child", false);
expect(["root", "grandchild", "sibling", "other-child"]);
click("grandchild", false);
expect(["root", "sibling", "other-child"]);
click("grandchild", true);
expect(["root", "grandchild", "sibling", "other-child"]);
// An already selected descendant must not stop recursive traversal.
click("root", true);
expect(["root", "child", "grandchild", "sibling", "other-child"]);
assert.equal(renders, 8);
change({target: {dataset: {}, checked: true}});
assert.equal(renders, 8);
'''.replace("HANDLER", handler)
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
