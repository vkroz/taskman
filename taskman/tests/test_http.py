"""HTTP API: auth, health, tasks, and dashboard rows."""
from __future__ import annotations

import json
from io import BytesIO

from taskman.httpapp import application
from taskman.store import create_task
from taskman.db import connect

TOKEN = "test-token-value"


def _call(method, path, body=None, token=TOKEN, query=""):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "taskman.local",
        "SERVER_PORT": "8080",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(raw),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(raw)),
    }
    if body is not None:
        environ["CONTENT_TYPE"] = "application/json"
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    payload = b"".join(application(environ, start_response))
    return int(captured["status"].split()[0]), payload


def test_health_is_open_and_the_page_has_no_task_data(database):
    code, body = _call("GET", "/health", token=None)
    assert code == 200
    assert json.loads(body) == {"status": "ok"}
    code, page = _call("GET", "/", token=None)
    assert code == 200
    assert b"flight control" in page.lower()
    assert b"Secret title" not in page


def test_missing_and_wrong_tokens_are_rejected(database):
    with connect(database) as conn:
        create_task(conn, "Secret title", "hidden", "task", None)
    code, body = _call("GET", "/api/tasks", token=None)
    assert code == 401
    assert b"Secret title" not in body
    code, body = _call("GET", "/api/dashboard", token="wrong-secret-value")
    assert code == 401
    assert b"wrong-secret-value" not in body
    assert b"Secret title" not in body


def test_health_reports_unavailable_when_postgres_is_down(monkeypatch):
    monkeypatch.setenv("TASKMAN_DATABASE_URL", "postgresql://taskman:taskman@127.0.0.1:1/taskman")
    code, body = _call("GET", "/health", token=None)
    assert code == 503
    assert json.loads(body)["status"] == "unavailable"


def test_create_show_and_dashboard_round_trip(database):
    code, body = _call("POST", "/api/tasks", {"title": "Write the API", "type": "task", "body": "Ship it"})
    assert code == 201
    task_id = json.loads(body)["id"]
    code, body = _call(
        "POST",
        f"/api/tasks/{task_id}/events",
        {"kind": "commit", "agent": "codex", "session": "s1", "commit_sha": "abc123def456", "message": "first cut"},
    )
    assert code == 201
    code, body = _call("GET", "/api/dashboard")
    assert code == 200
    row = json.loads(body)["rows"][0]
    assert row["title"] == "Write the API"
    assert row["agent"] == "codex"
    assert row["last_event"] == "commit"
    assert "abc123def456"[:12] in row["detail"]
    code, body = _call("POST", "/api/tasks", {"title": "", "type": "task", "body": ""})
    assert code == 400
