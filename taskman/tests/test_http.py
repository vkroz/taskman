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
    code, body = _call("GET", "/ready", token=None)
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


def test_health_stays_up_when_readiness_cannot_reach_postgres(monkeypatch):
    def fail():
        raise RuntimeError("postgres is down")

    monkeypatch.setattr("taskman.httpapp.ping", fail)
    code, body = _call("GET", "/health", token=None)
    assert code == 200
    assert json.loads(body) == {"status": "ok"}
    code, body = _call("GET", "/ready", token=None)
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


def test_bearer_scheme_is_case_insensitive(database):
    code, body = _call("POST", "/api/tasks", {"title": "Case", "type": "task", "body": ""})
    assert code == 201
    task_id = json.loads(body)["id"]
    raw = b""
    environ_token = "bEaReR " + TOKEN
    # Reuse _call's header builder by passing the mixed-case token through a direct call.
    from io import BytesIO
    from taskman.httpapp import application

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": f"/api/tasks/{task_id}",
        "QUERY_STRING": "",
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
        "CONTENT_LENGTH": "0",
        "HTTP_AUTHORIZATION": environ_token,
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    payload = b"".join(application(environ, start_response))
    assert int(captured["status"].split()[0]) == 200
    assert json.loads(payload)["title"] == "Case"


def test_unknown_event_kind_is_400_and_oversized_body_is_413(database):
    code, body = _call("POST", "/api/tasks", {"title": "Events", "type": "task", "body": ""})
    task_id = json.loads(body)["id"]
    code, body = _call("POST", f"/api/tasks/{task_id}/events", {"kind": "nope"})
    assert code == 400
    assert b"Unknown event kind" in body
    from io import BytesIO
    from taskman.httpapp import application

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/tasks",
        "QUERY_STRING": "",
        "SERVER_NAME": "taskman.local",
        "SERVER_PORT": "8080",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(b"{}"),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": "2000000",
        "CONTENT_TYPE": "application/json",
        "HTTP_AUTHORIZATION": f"Bearer {TOKEN}",
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    application(environ, start_response)
    assert int(captured["status"].split()[0]) == 413


def test_pull_rejects_an_empty_agent(database):
    code, body = _call("POST", "/api/tasks", {"title": "Claim", "type": "task", "body": ""})
    task_id = json.loads(body)["id"]
    code, body = _call("POST", f"/api/tasks/{task_id}/pull", {"agent": "  ", "session": ""})
    assert code == 400
    assert b"Agent is required" in body


def test_complete_and_events_reject_an_empty_agent(database):
    code, body = _call("POST", "/api/tasks", {"title": "Claim", "type": "task", "body": ""})
    task_id = json.loads(body)["id"]
    code, body = _call("POST", f"/api/tasks/{task_id}/complete", {"message": "done", "agent": " "})
    assert code == 400
    assert b"Agent is required" in body
    code, body = _call(
        "POST",
        f"/api/tasks/{task_id}/events",
        {"kind": "pull_request", "agent": "", "pr_url": "https://example.com/pull/1"},
    )
    assert code == 400
    assert b"Agent is required" in body
    code, body = _call("GET", f"/api/tasks/{task_id}/context")
    assert json.loads(body)["events"] == []


def test_duplicate_event_post_returns_200(database):
    code, body = _call("POST", "/api/tasks", {"title": "Events", "type": "task", "body": ""})
    task_id = json.loads(body)["id"]
    commit = {"kind": "commit", "agent": "codex", "commit_sha": "abc123def456", "message": "first cut"}
    code, body = _call("POST", f"/api/tasks/{task_id}/events", commit)
    assert code == 201
    first = json.loads(body)
    code, body = _call(
        "POST",
        f"/api/tasks/{task_id}/events",
        {"kind": "commit", "agent": "git", "commit_sha": "abc123def456", "message": "again"},
    )
    assert code == 200
    assert json.loads(body)["id"] == first["id"]
    completion = {"kind": "completion", "agent": "codex", "session": "sess", "message": "shipped"}
    code, body = _call("POST", f"/api/tasks/{task_id}/events", completion)
    assert code == 201
    done = json.loads(body)
    code, body = _call("POST", f"/api/tasks/{task_id}/events", {**completion, "message": "shipped again"})
    assert code == 200
    assert json.loads(body)["id"] == done["id"]
    code, body = _call(
        "POST",
        f"/api/tasks/{task_id}/events",
        {"kind": "commit", "agent": "codex", "commit_sha": "fff999", "message": "second"},
    )
    assert code == 201
    code, body = _call("GET", f"/api/tasks/{task_id}/context")
    events = json.loads(body)["events"]
    assert [event["kind"] for event in events] == ["commit", "completion", "commit"]
    assert len([event for event in events if event["commit_sha"] == "abc123def456"]) == 1
