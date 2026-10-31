"""WSGI application served by gunicorn inside the Taskman container.

Binds are chosen by ``taskman serve`` (``0.0.0.0`` and ``PORT``). This module
only handles HTTP. ``/health`` is unauthenticated so a process probe can run
before a client presents ``TASKMAN_ACCESS_TOKEN``. Every other route requires
that token, either as ``Authorization: Bearer`` or as the HttpOnly session
cookie set by ``POST /login``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from urllib.parse import parse_qs

from taskman.access import configured_access_token, secrets_equal
from taskman.commands.new import get_tasks_dir
from taskman.commands.ui import _HTTPResponse, _build_get_response, _build_post_response, _json_response
from taskman.model.locking import mutation_lock

_COOKIE = "taskman_access"
_SESSION_LABEL = b"taskman-access-v1"
_LOGIN_LIMIT = 4096
_LOGOUT_FORM = (
    '<form method="post" action="/logout" style="display:inline">'
    '<button type="submit">Log out</button></form>'
).encode("utf-8")
_NEW_TASK_BUTTON = '<button class="primary" id="new">＋ New task</button>'.encode("utf-8")


def _session_value(token: str) -> str:
    return hmac.new(token.encode("utf-8"), _SESSION_LABEL, hashlib.sha256).hexdigest()


def _cookie_matches(header: str, token: str) -> bool:
    expected = _session_value(token)
    for part in header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == _COOKIE and secrets_equal(value, expected):
            return True
    return False


def _authorization(environ: dict) -> str:
    return environ.get("HTTP_AUTHORIZATION", "")


def _access(environ: dict, token: str) -> str:
    """Return ``ok``, ``rejected``, or ``missing``."""
    header = _authorization(environ)
    if header:
        scheme, separator, provided = header.partition(" ")
        if separator and scheme == "Bearer" and secrets_equal(provided, token):
            return "ok"
        return "rejected"
    if _cookie_matches(environ.get("HTTP_COOKIE", ""), token):
        return "ok"
    return "missing"


def _status_line(status: int) -> str:
    phrases = {
        200: "OK",
        201: "Created",
        302: "Found",
        303: "See Other",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    return f"{status} {phrases.get(status, 'Error')}"


def _cookie_header(token: str, max_age: int) -> str:
    value = _session_value(token) if max_age else ""
    return f"{_COOKIE}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}"


def _send(start_response, response: _HTTPResponse, extra: list[tuple[str, str]] | None = None):
    headers = [
        ("Content-Type", response.content_type),
        ("Content-Length", str(len(response.body))),
        ("Cache-Control", response.cache_control or "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ]
    if extra:
        headers.extend(extra)
    start_response(_status_line(response.status), headers)
    return [response.body]


def _redirect(start_response, location: str, set_cookie: str | None = None):
    body = b""
    headers = [
        ("Location", location),
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", "0"),
        ("Cache-Control", "no-store"),
    ]
    if set_cookie:
        headers.append(("Set-Cookie", set_cookie))
    start_response("303 See Other" if set_cookie is not None else "302 Found", headers)
    return [body]


def _health(tasks_dir) -> _HTTPResponse:
    writable = tasks_dir.is_dir() and os.access(tasks_dir, os.W_OK)
    if writable:
        return _json_response(200, {"status": "ok"})
    return _json_response(503, {"status": "unavailable"})


def _login_page(message: str = "") -> _HTTPResponse:
    notice = f'<p role="alert">{message}</p>' if message else ""
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Taskman · Sign in</title>
  <style>
    body {{ font-family: -apple-system, system-ui, sans-serif; background: #f7f8fb; color: #202738; margin: 0; }}
    main {{ max-width: 28rem; margin: 4rem auto; padding: 1.5rem; background: white; border: 1px solid #e4e7ef; border-radius: 12px; }}
    label {{ display: block; font-weight: 600; margin-bottom: 0.5rem; }}
    input {{ width: 100%; padding: 0.6rem; font: inherit; box-sizing: border-box; }}
    button {{ margin-top: 1rem; font: inherit; padding: 0.6rem 1rem; background: #5145cd; color: white; border: 0; border-radius: 7px; }}
    button:focus-visible, input:focus-visible {{ outline: 3px solid #aea6ff; outline-offset: 2px; }}
    p[role="alert"] {{ color: #b42318; }}
  </style>
</head>
<body>
  <main>
    <h1>Taskman</h1>
    <p>Enter the access token configured for this server.</p>
    {notice}
    <form method="post" action="/login">
      <label for="token">Access token</label>
      <input id="token" name="token" type="password" autocomplete="current-password" required>
      <button type="submit">Continue</button>
    </form>
  </main>
</body>
</html>
"""
    return _HTTPResponse(200 if not message else 401, html.encode("utf-8"), "text/html; charset=utf-8", "no-store")


def _submitted_token(environ: dict, body: bytes) -> str:
    content_type = environ.get("CONTENT_TYPE", "")
    if content_type == "application/json":
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        token = data.get("token", "")
        if not isinstance(token, str):
            raise ValueError("Expected a token")
        return token
    fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=5)
    values = fields.get("token", [])
    return values[0] if values else ""


def _read_limited(environ: dict, limit: int) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError as exc:
        raise ValueError("Invalid request size") from exc
    if length < 0 or length > limit:
        raise ValueError("Invalid request size")
    return environ["wsgi.input"].read(length)


def _with_logout(response: _HTTPResponse) -> _HTTPResponse:
    if _NEW_TASK_BUTTON not in response.body:
        return response
    body = response.body.replace(_NEW_TASK_BUTTON, _LOGOUT_FORM + _NEW_TASK_BUTTON, 1)
    return _HTTPResponse(response.status, body, response.content_type, response.cache_control)


def application(environ, start_response):
    """WSGI entry point. Read the token on each request so config stays explicit."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    tasks_dir = get_tasks_dir()

    try:
        if method == "GET" and path == "/health":
            return _send(start_response, _health(tasks_dir))

        token = configured_access_token()
        if token is None:
            return _send(
                start_response,
                _json_response(503, {"error": "TASKMAN_ACCESS_TOKEN is not configured."}),
            )

        if path == "/login" and method == "GET":
            if _access(environ, token) == "ok":
                return _redirect(start_response, "/")
            return _send(start_response, _login_page())
        if path == "/login" and method == "POST":
            try:
                submitted = _submitted_token(environ, _read_limited(environ, _LOGIN_LIMIT))
            except (ValueError, UnicodeError):
                return _send(start_response, _login_page("The access token was not accepted."))
            if not secrets_equal(submitted, token):
                return _send(start_response, _login_page("The access token was not accepted."))
            return _redirect(start_response, "/", _cookie_header(token, 30 * 24 * 60 * 60))
        if path == "/logout" and method == "POST":
            return _redirect(start_response, "/login", _cookie_header(token, 0))

        access = _access(environ, token)
        if access != "ok":
            if access == "missing" and method == "GET" and not path.startswith("/api/"):
                return _redirect(start_response, "/login")
            return _send(
                start_response,
                _json_response(401, {"error": "Authentication required."}),
                [("WWW-Authenticate", "Bearer")],
            )

        if method == "GET":
            with mutation_lock(tasks_dir):
                response = _build_get_response(tasks_dir, path)
            if path == "/" and response.content_type.startswith("text/html") and not response.send_error:
                response = _with_logout(response)
            if response.send_error:
                message = response.error_message or "Not found"
                response = _HTTPResponse(
                    response.status,
                    message.encode("utf-8"),
                    "text/plain; charset=utf-8",
                    "no-store",
                )
            return _send(start_response, response)

        if method == "POST":
            if environ.get("HTTP_X_TASKMAN") != "1":
                return _send(
                    start_response,
                    _json_response(403, {"error": "Only Taskman UI requests are accepted."}),
                )
            return _send(
                start_response,
                _build_post_response(
                    tasks_dir,
                    path,
                    environ.get("CONTENT_TYPE"),
                    environ.get("CONTENT_LENGTH"),
                    environ["wsgi.input"].read,
                ),
            )

        return _send(start_response, _HTTPResponse(405, b"Method not allowed\n", cache_control="no-store"))
    except Exception:
        import traceback

        traceback.print_exc()
        return _send(start_response, _json_response(500, {"error": "Internal error"}))
