"""Network server: token gate, health check, file persistence, and process bind."""
from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.serve import ServeConfigError, gunicorn_argv, serve_config
from taskman.commands.ui import _make_handler
from taskman.model.names import parse_name
from taskman.wsgi import application

runner = CliRunner()
TOKEN = "test-token-value"


def _call(method, path, *, tasks, body=b"", headers=None, content_type=None):
    header_map = dict(headers or {})
    if content_type:
        header_map["Content-Type"] = content_type
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "SERVER_NAME": "taskman.local",
        "SERVER_PORT": "8080",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(body),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(body)),
    }
    for key, value in header_map.items():
        if key.lower() == "content-type":
            environ["CONTENT_TYPE"] = value
        else:
            environ["HTTP_" + key.upper().replace("-", "_")] = value
    captured: dict = {}

    def start_response(status, response_headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = response_headers

    payload = b"".join(application(environ, start_response))
    code = int(captured["status"].split()[0])
    return code, captured["headers"], payload


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path))
    return tmp_path


def _header(headers, name):
    for key, value in headers:
        if key.lower() == name.lower():
            return value
    return None


def test_health_is_open_and_reports_writable_storage(tasks):
    code, _, body = _call("GET", "/health", tasks=tasks)
    assert code == 200
    assert json.loads(body) == {"status": "ok"}
    assert TOKEN.encode() not in body


def test_health_is_unavailable_when_the_task_directory_is_missing(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(missing))
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", TOKEN)
    code, _, body = _call("GET", "/health", tasks=missing)
    assert code == 503
    assert json.loads(body)["status"] == "unavailable"


def test_unconfigured_server_fails_closed_except_health(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path))
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", "")
    health, _, _ = _call("GET", "/health", tasks=tmp_path)
    page, _, body = _call("GET", "/", tasks=tmp_path)
    assert health == 200
    assert page == 503
    assert b"TASKMAN_ACCESS_TOKEN" in body


def test_missing_credentials_redirect_pages_and_reject_api(tasks):
    code, headers, body = _call("GET", "/", tasks=tasks)
    assert code == 302
    assert _header(headers, "Location") == "/login"
    assert b"HiddenFromAnonymous" not in body

    item = runner.invoke(
        app,
        ["new", "--title", "HiddenFromAnonymous", "--type", "task"],
        env={"TASKMAN_TASKS_DIR": str(tasks)},
    )
    assert item.exit_code == 0, item.output
    item_id = parse_name(Path(item.output.strip()).name).item_id
    code, _, body = _call("GET", f"/api/show/{item_id}", tasks=tasks)
    assert code == 401
    assert b"HiddenFromAnonymous" not in body
    assert b"Authentication required." in body


def test_wrong_bearer_is_rejected_without_echoing_the_secret(tasks):
    code, headers, body = _call(
        "GET",
        "/",
        tasks=tasks,
        headers={"Authorization": "Bearer wrong-secret-value"},
    )
    assert code == 401
    assert b"wrong-secret-value" not in body
    assert _header(headers, "WWW-Authenticate") == "Bearer"


def test_bearer_token_serves_the_workspace(tasks):
    code, _, body = _call(
        "GET",
        "/",
        tasks=tasks,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert code == 200
    assert b'aria-label="Work views"' in body
    assert b'action="/logout"' in body


def test_login_sets_a_cookie_and_a_wrong_token_does_not(tasks):
    bad_body = urlencode({"token": "wrong-secret-value"}).encode()
    code, headers, body = _call(
        "POST",
        "/login",
        tasks=tasks,
        body=bad_body,
        content_type="application/x-www-form-urlencoded",
    )
    assert code == 401
    assert _header(headers, "Set-Cookie") is None
    assert b"wrong-secret-value" not in body

    good_body = urlencode({"token": TOKEN}).encode()
    code, headers, _ = _call(
        "POST",
        "/login",
        tasks=tasks,
        body=good_body,
        content_type="application/x-www-form-urlencoded",
    )
    assert code == 303
    cookie = _header(headers, "Set-Cookie")
    assert cookie is not None
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie
    assert TOKEN not in cookie
    value = cookie.split(";", 1)[0]
    code, _, body = _call("GET", "/", tasks=tasks, headers={"Cookie": value})
    assert code == 200
    assert b'aria-label="Work views"' in body

    code, headers, _ = _call("POST", "/logout", tasks=tasks, headers={"Cookie": value})
    assert code == 303
    cleared = _header(headers, "Set-Cookie")
    assert "Max-Age=0" in cleared
    code, headers, _ = _call("GET", "/", tasks=tasks, headers={"Cookie": cleared.split(";", 1)[0]})
    assert code == 302


def test_raw_token_is_not_accepted_as_the_session_cookie(tasks):
    code, _, _ = _call("GET", "/", tasks=tasks, headers={"Cookie": f"taskman_access={TOKEN}"})
    assert code == 302


def test_authenticated_create_persists_for_the_cli_and_rejects_a_bare_post(tasks):
    payload = json.dumps({"title": "Stored on disk", "type": "task", "body": "Keep this."}).encode()
    code, _, body = _call(
        "POST",
        "/api/items",
        tasks=tasks,
        body=payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {TOKEN}", "X-Taskman": "1"},
    )
    assert code == 201, body
    item_id = json.loads(body)["id"]
    shown = runner.invoke(
        app,
        ["show", item_id, "--json"],
        env={"TASKMAN_TASKS_DIR": str(tasks)},
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["frontmatter"]["title"] == "Stored on disk"

    before = set(tasks.rglob("*.md"))
    code, _, _ = _call(
        "POST",
        "/api/items",
        tasks=tasks,
        body=payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert code == 403
    assert set(tasks.rglob("*.md")) == before


def test_wsgi_server_accepts_connections_on_all_interfaces(tasks):
    from wsgiref.simple_server import make_server

    httpd = make_server("0.0.0.0", 0, application)
    assert httpd.server_address[0] == "0.0.0.0"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        port = httpd.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            assert response.status == 200
            assert json.load(response) == {"status": "ok"}
        request = Request(
            f"http://127.0.0.1:{port}/",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with urlopen(request, timeout=3) as response:
            page = response.read().decode()
        assert 'id="tree"' in page
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_local_ui_still_rejects_a_non_local_host(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        port = server.server_port
        local = HTTPConnection("127.0.0.1", port, timeout=3)
        local.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        assert local.getresponse().status == 200
        remote = HTTPConnection("127.0.0.1", port, timeout=3)
        remote.request("GET", "/", headers={"Host": "public.example"})
        assert remote.getresponse().status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_serve_refuses_to_start_without_a_token(tmp_path):
    result = runner.invoke(
        app,
        ["serve"],
        env={"TASKMAN_ACCESS_TOKEN": "   ", "TASKMAN_TASKS_DIR": str(tmp_path)},
    )
    text = (result.output or "") + (result.stderr or "")
    assert result.exit_code == 1
    assert "TASKMAN_ACCESS_TOKEN" in text


def test_serve_execs_gunicorn_on_all_interfaces(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("taskman.commands.serve._exec", lambda program, argv: calls.append(argv))
    tasks = tmp_path / "tasks"
    result = runner.invoke(
        app,
        ["serve"],
        env={
            "TASKMAN_ACCESS_TOKEN": TOKEN,
            "PORT": "9001",
            "TASKMAN_WEB_WORKERS": "1",
            "TASKMAN_TASKS_DIR": str(tasks),
        },
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0] == "gunicorn"
    assert "0.0.0.0:9001" in calls[0]
    assert "taskman.wsgi:application" in calls[0]
    assert "--workers" in calls[0] and calls[0][calls[0].index("--workers") + 1] == "1"
    assert TOKEN not in " ".join(calls[0])
    assert TOKEN not in result.output
    assert tasks.is_dir()


def test_serve_rejects_bad_port_and_worker_count(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path))
    monkeypatch.setenv("PORT", "0")
    with pytest.raises(ServeConfigError):
        serve_config()
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("TASKMAN_WEB_WORKERS", "nope")
    with pytest.raises(ServeConfigError):
        serve_config()


def test_gunicorn_bind_defaults_to_port_8080_and_two_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("TASKMAN_WEB_WORKERS", raising=False)
    config = serve_config()
    argv = gunicorn_argv(config)
    assert config.bind == "0.0.0.0:8080"
    assert argv[argv.index("--workers") + 1] == "2"
    assert "127.0.0.1" not in argv


def test_deploy_files_match_the_container_contract():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    compose = (root / "docker-compose.yml").read_text()
    example = (root / ".env.example").read_text()
    ignore = (root / ".gitignore").read_text()
    assert 'CMD ["taskman", "serve"]' in dockerfile
    assert "TASKMAN_TASKS_DIR=/data/tasks" in dockerfile
    assert "taskman-data:/data" in compose
    assert "unless-stopped" in compose
    assert "TASKMAN_ACCESS_TOKEN" in example
    assert "replace-with-a-long-random-token" in example
    assert ".env" in ignore.splitlines()

