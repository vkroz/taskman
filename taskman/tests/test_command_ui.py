"""Tests for the ui command — data prep + server smoke."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from tests.workflow_helpers import prepare
from taskman.commands.ui import (
    _build_mermaid,
    _Item,
    _make_handler,
    collect_page,
    render_page,
)
from taskman.model.layout import find_item_by_id
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file, write_file

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str, item_type: str = "task") -> str:
    r = runner.invoke(
        app, ["new", "--title", title, "--type", item_type], env=_env(tmp_path)
    )
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


# ---- data aggregation ----


def test_collect_page_empty_tree(tmp_path: Path) -> None:
    (tmp_path / "backlog").mkdir()
    page = collect_page(tmp_path)
    assert page.items == []
    assert page.ready == []
    assert page.blocked == []
    assert page.recent == []


def test_collect_page_ready_and_blocked(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends(tmp_path, b, [a])
    page = collect_page(tmp_path)
    ready_ids = {i.item_id for i in page.ready}
    blocked_ids = {i.item_id for i in page.blocked}
    assert a in ready_ids
    assert b in blocked_ids


def test_collect_page_includes_done_in_items_but_not_ready(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    runner.invoke(app, ["close", a], env=_env(tmp_path))
    page = collect_page(tmp_path)
    assert any(i.item_id == a for i in page.items)
    assert not any(i.item_id == a for i in page.ready)
    assert not any(i.item_id == a for i in page.blocked)


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


def test_render_page_contains_required_sections(tmp_path: Path) -> None:
    _new(tmp_path, "X")
    page = collect_page(tmp_path)
    html = render_page(page)
    assert 'id="tree"' in html
    assert 'id="ready"' in html
    assert 'id="blocked"' in html
    assert 'id="recent"' in html
    assert 'id="graph"' in html
    assert "mermaid" in html
    assert "Filter" in html  # search input placeholder


def test_render_page_escapes_html(tmp_path: Path) -> None:
    """Item titles with HTML characters are escaped."""
    item_id = _new(tmp_path, "X")
    # Manually corrupt the title to contain a script tag.
    path = find_item_by_id(tmp_path, item_id)
    data, body = read_file(path)
    data["title"] = "<script>alert(1)</script>"
    write_file(path, data, body)
    page = collect_page(tmp_path)
    html = render_page(page)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# ---- server smoke ----


def test_server_smoke_serves_html(tmp_path: Path) -> None:
    """Start the server on an ephemeral port, fetch /, assert 200 + section markers."""
    _new(tmp_path, "Smoke Test Item")
    handler = _make_handler(tmp_path)
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
        assert 'id="graph"' in body
        assert "smoke_test_item" in body
    finally:
        server.shutdown()
        server.server_close()


def test_server_api_show_returns_json(tmp_path: Path) -> None:
    """The /api/show/<id> endpoint returns JSON with frontmatter + body."""
    item_id = _new(tmp_path, "Api Test")
    handler = _make_handler(tmp_path)
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


def test_server_api_show_missing_returns_404(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    server = ThreadingHTTPServer(("localhost", 0), handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/show/999999999", timeout=3)
            raised = False
        except urllib.error.HTTPError as e:
            assert e.code == 404
            raised = True
        assert raised
    finally:
        server.shutdown()
        server.server_close()


# ---- --stop ----


def test_ui_stop_when_no_server_running(tmp_path: Path, monkeypatch) -> None:
    """--stop with no running server reports cleanly, exits 0."""
    monkeypatch.setattr("taskman.commands.ui._pid_file", lambda: tmp_path / "ui.pid")
    r = runner.invoke(app, ["ui", "--stop"], env=_env(tmp_path))
    assert r.exit_code == 0
