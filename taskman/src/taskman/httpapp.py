"""WSGI application: dashboard, task API, liveness, and readiness.

The browser polls ``/api/dashboard``. The server does not open connections to agents.
``GET /health`` does not touch Postgres. ``GET /ready`` runs ``SELECT 1``.
"""
from __future__ import annotations

import json
from taskman.access import configured_access_token, secrets_equal
from taskman.db import ping
from taskman.store import (
    TaskmanError,
    complete_task,
    context,
    create_task,
    dashboard,
    get_task,
    list_tasks,
    pull_task,
    ready_tasks,
    set_dependencies,
    update_task,
    add_event,
    with_connection,
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Taskman · Flight control</title>
  <style>
    :root { color: #202738; background: #f7f8fb; font: 14px/1.4 Inter, -apple-system, system-ui, sans-serif; }
    body { margin: 0; }
    header, main { padding: 16px 20px; }
    header { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; border-bottom: 1px solid #e4e7ef; }
    h1 { margin: 0; font-size: 20px; }
    label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 600; }
    input { font: inherit; padding: 8px; min-width: 16rem; }
    button { font: inherit; padding: 8px 12px; border: 1px solid #d0d5dd; border-radius: 7px; background: white; }
    button.primary { background: #5145cd; color: white; border-color: #5145cd; }
    button:focus-visible, input:focus-visible { outline: 3px solid #aea6ff; outline-offset: 2px; }
    #state { min-height: 1.2em; color: #667085; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #e4e7ef; vertical-align: top; }
    th { font-size: 12px; color: #667085; }
    .done { color: #667085; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Taskman flight control</h1>
      <p id="state">Enter the access token.</p>
    </div>
    <label>Access token
      <input id="token" type="password" autocomplete="current-password">
    </label>
    <button class="primary" id="save" type="button">Use token</button>
    <button id="clear" type="button">Clear</button>
  </header>
  <main>
    <table>
      <thead>
        <tr>
          <th>Task</th><th>Title</th><th>Status</th><th>Agent</th><th>Session</th>
          <th>Last event</th><th>When</th><th>Detail</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    const state = document.getElementById('state');
    const rows = document.getElementById('rows');
    const tokenInput = document.getElementById('token');
    function token() { return sessionStorage.getItem('taskman_token') || ''; }
    function cell(text) {
      const td = document.createElement('td');
      td.textContent = text || '';
      return td;
    }
    async function refresh() {
      const value = token();
      if (!value) {
        state.textContent = 'Enter the access token. It stays in this browser tab.';
        rows.replaceChildren();
        return;
      }
      try {
        const response = await fetch('/api/dashboard', {headers: {Authorization: 'Bearer ' + value}});
        if (response.status === 401) {
          state.textContent = 'Token was rejected.';
          rows.replaceChildren();
          return;
        }
        if (!response.ok) throw new Error('Dashboard request failed.');
        const data = await response.json();
        const next = document.createDocumentFragment();
        data.rows.forEach((row) => {
          const tr = document.createElement('tr');
          if (row.status === 'done') tr.className = 'done';
          [String(row.id), row.title, row.status, row.agent, row.session, row.last_event, row.last_activity, row.detail]
            .forEach((value) => tr.appendChild(cell(value)));
          next.appendChild(tr);
        });
        rows.replaceChildren(next);
        state.textContent = data.rows.length ? 'Updated from reported agent events.' : 'No tasks yet.';
      } catch (error) {
        state.textContent = error.message;
      }
    }
    document.getElementById('save').addEventListener('click', () => {
      sessionStorage.setItem('taskman_token', tokenInput.value.trim());
      tokenInput.value = '';
      refresh();
    });
    document.getElementById('clear').addEventListener('click', () => {
      sessionStorage.removeItem('taskman_token');
      refresh();
    });
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def _status_line(status: int) -> str:
    phrases = {
        200: "OK",
        201: "Created",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        413: "Payload Too Large",
        405: "Method Not Allowed",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    return f"{status} {phrases.get(status, 'Error')}"


def _send(start_response, status: int, body: bytes, content_type: str, extra: list[tuple[str, str]] | None = None):
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ]
    if extra:
        headers.extend(extra)
    start_response(_status_line(status), headers)
    return [body]


def _json(start_response, status: int, payload, extra=None):
    return _send(start_response, status, json.dumps(payload).encode("utf-8"), "application/json", extra)


def _authorized(environ: dict, token: str) -> bool:
    header = environ.get("HTTP_AUTHORIZATION", "")
    scheme, separator, provided = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return False
    return secrets_equal(provided, token)


def _read_json(environ: dict, limit: int = 1_000_000) -> dict:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError as exc:
        raise TaskmanError(400, "Invalid request size.") from exc
    if length < 0:
        raise TaskmanError(400, "Invalid request size.")
    if length > limit:
        raise TaskmanError(413, "Request body is too large.")
    raw = environ["wsgi.input"].read(length) if length else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise TaskmanError(400, "Expected JSON.") from exc
    if not isinstance(data, dict):
        raise TaskmanError(400, "Expected an object.")
    return data


def _task_id(path: str, prefix: str) -> tuple[int, str]:
    rest = path[len(prefix) :]
    ident, _, tail = rest.partition("/")
    if not ident.isdigit():
        raise TaskmanError(404, "task not found")
    return int(ident), tail


def _optional_int(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskmanError(400, f"{name} must be an integer.")
    return value


def _text(data: dict, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise TaskmanError(400, f"{key} must be text.")
    return value


def _route(method: str, path: str, data: dict):
    if method == "POST" and path == "/api/tasks":
        parent = _optional_int(data.get("parent_id"), "parent_id")
        task = with_connection(
            create_task,
            _text(data, "title"),
            _text(data, "body"),
            _text(data, "type", "task") or "task",
            parent,
        )
        return 201, task
    if path == "/api/dashboard" and method == "GET":
        return 200, {"rows": with_connection(dashboard)}
    if path == "/api/ready" and method == "GET":
        return 200, {"tasks": with_connection(ready_tasks)}
    if not path.startswith("/api/tasks/"):
        raise TaskmanError(404, "Unknown endpoint.")
    task_id, tail = _task_id(path, "/api/tasks/")
    if tail == "" and method == "GET":
        return 200, with_connection(get_task, task_id)
    if tail == "" and method == "PATCH":
        fields = {}
        if "title" in data:
            fields["title"] = _text(data, "title")
        if "body" in data:
            fields["body"] = _text(data, "body")
        if "status" in data:
            fields["status"] = _text(data, "status")
        if not fields:
            raise TaskmanError(400, "Nothing to update.")
        return 200, with_connection(update_task, task_id, **fields)
    if tail == "dependencies" and method == "PUT":
        raw = data.get("depends_on", [])
        if not isinstance(raw, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in raw):
            raise TaskmanError(400, "depends_on must be a list of task ids.")
        ids = with_connection(set_dependencies, task_id, raw)
        return 200, {"task_id": task_id, "depends_on": ids}
    if tail == "context" and method == "GET":
        return 200, with_connection(context, task_id)
    if tail == "events" and method == "POST":
        event, created = with_connection(
            add_event,
            task_id,
            _text(data, "kind"),
            agent=_text(data, "agent"),
            session=_text(data, "session"),
            commit_sha=_text(data, "commit_sha"),
            message=_text(data, "message"),
            pr_url=_text(data, "pr_url"),
        )
        # 201 only when a row was inserted. A duplicate commit sha, or a
        # completion that repeats the latest one for the same agent and
        # session, returns 200 and the stored event. 409 would turn a
        # successful retry into an error for the CLI and the hooks.
        return (201 if created else 200), event
    if tail == "pull" and method == "POST":
        return 200, with_connection(pull_task, task_id, _text(data, "agent"), _text(data, "session"))
    if tail == "complete" and method == "POST":
        return 200, with_connection(
            complete_task, task_id, _text(data, "message"), _text(data, "agent"), _text(data, "session")
        )
    raise TaskmanError(404, "Unknown endpoint.")


def dispatch(environ, start_response, token):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query_status = None
    if path == "/api/tasks" and method == "GET":
        from urllib.parse import parse_qs

        query_status = parse_qs(environ.get("QUERY_STRING", "")).get("status", [None])[0]
    try:
        if method == "GET" and path == "/health":
            return _json(start_response, 200, {"status": "ok"})
        if method == "GET" and path == "/ready":
            try:
                ping()
            except Exception:
                return _json(start_response, 503, {"status": "unavailable"})
            return _json(start_response, 200, {"status": "ok"})
        if method == "GET" and path == "/":
            return _send(start_response, 200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if token is None:
            return _json(start_response, 503, {"error": "TASKMAN_ACCESS_TOKEN is not configured."})
        if not _authorized(environ, token):
            return _json(
                start_response,
                401,
                {"error": "Authentication required."},
                [("WWW-Authenticate", "Bearer")],
            )
        if method == "GET" and path == "/api/tasks":
            status, payload = 200, {"tasks": with_connection(list_tasks, query_status)}
        elif method in {"POST", "PUT", "PATCH"}:
            status, payload = _route(method, path, _read_json(environ))
        else:
            status, payload = _route(method, path, {})
        return _json(start_response, status, payload)
    except TaskmanError as exc:
        return _json(start_response, exc.status, {"error": exc.message})
    except Exception:
        import traceback

        traceback.print_exc()
        return _json(start_response, 500, {"error": "Internal error"})


def application(environ, start_response):
    """Production entry point. Reads the token from the environment per request."""
    return dispatch(environ, start_response, configured_access_token())


def make_application(token: str):
    """WSGI app with a fixed token. Tests use this so the client can change os.environ."""

    def _app(environ, start_response):
        return dispatch(environ, start_response, token)

    return _app
