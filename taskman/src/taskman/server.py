"""Taskman server: the HTTP API that the CLI calls.

The server alone opens the database. A caller proves who they are with a
token (``Authorization: Bearer ...``); the person of the token is the author
of every change. Changes arrive as whole operations and run here, under the
store lock, so the lifecycle rules are checked next to the data.

In local mode the server also serves the workspace page and its data calls,
without sign-in, to a browser on the same computer. One person, named in the
settings, is the author of the changes that the page makes.

With browser login (hosted mode), the server serves the workspace page to a
person who signs in with Google. A session cookie names the person, who is
the author of the changes that the page makes. Each change of the page must
come from the server's own origin and carry the page's CSRF (cross-site
request forgery) key.
"""
from __future__ import annotations

import hmac
import inspect
import json
import re
import types
import typing
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from taskman import lifecycle, workspace
from taskman.model.ordering import reorder_item
from taskman.login import (LOGIN_COOKIE, LOGIN_TIME, SESSION_COOKIE, SESSION_TIME, LoginError, Logins, csrf_token,
                           error_page, signed_out_page, ui_error_page, ui_not_set_up_page)
from taskman.storage.accounts import list_tokens, person_for_token, revoke_own_token, revoke_token
from taskman.storage.sql_store import SqlStore
from taskman.storage.store import Item, StorageError, item_data

API = "/api/v1"
MAX_REQUEST_BYTES = 1024 * 1024
_ACTOR_LIMIT = 35
_TOKEN_REVOKE = re.compile(r"^/api/v1/tokens/(\d{1,18})/revoke$")
_TOKEN_FIELDS = ("id", "label", "created_at", "expires_at", "last_used_at", "revoked_at")

OPERATIONS: dict[str, Callable] = {
    operation.__name__: operation
    for operation in (
        lifecycle.create, lifecycle.finalize, lifecycle.update_body, lifecycle.record,
        lifecycle.set_dependencies, lifecycle.discard, lifecycle.move, lifecycle.convert,
        lifecycle.close, lifecycle.edit, lifecycle.assign, lifecycle.label, lifecycle.set_repositories, reorder_item,
    )
}


def _public(item: Item) -> dict[str, Any]:
    """An item for a caller. The location names the server, not its database type."""
    return item_data(item) | {"location": f"server:{item.id}"}


class _Refused(Exception):
    """A request that the server answers with an error status."""

    def __init__(self, status: int, kind: str, message: str, usage: bool = False) -> None:
        super().__init__(message)
        self.status, self.kind, self.message, self.usage = status, kind, message, usage


def _matches(value: Any, hint: Any) -> bool:
    """Whether a JSON value fits a parameter annotation of an operation."""
    if hint is Any:
        return True
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        return any(_matches(value, option) for option in typing.get_args(hint))
    if hint is type(None):
        return value is None
    if origin is list:
        (inner,) = typing.get_args(hint)
        return isinstance(value, list) and all(_matches(entry, inner) for entry in value)
    if hint is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, hint)


def _arguments(operation: Callable, given: Any) -> dict[str, Any]:
    """The checked keyword arguments for an operation."""
    if not isinstance(given, dict):
        raise _Refused(400, "request", "arguments must be an object")
    parameters = list(inspect.signature(operation).parameters.values())[1:]
    hints = typing.get_type_hints(operation)
    names = {parameter.name for parameter in parameters}
    unknown = sorted(set(given) - names)
    if unknown:
        raise _Refused(400, "request", f"unknown argument: {unknown[0]}")
    for parameter in parameters:
        if parameter.name not in given:
            if parameter.default is inspect.Parameter.empty:
                raise _Refused(400, "request", f"missing argument: {parameter.name}")
        elif not _matches(given[parameter.name], hints.get(parameter.name, Any)):
            raise _Refused(400, "request", f"wrong type for argument: {parameter.name}")
    return dict(given)


def own_tokens(store: SqlStore, person: str) -> list[dict[str, Any]]:
    """The tokens of ``person``, without their secrets and without the person's name."""
    return [{field: row[field] for field in _TOKEN_FIELDS} for row in list_tokens(store, person)]


def author_name(person: str, actor: Any) -> str:
    """The author of a change: the person, or ``<actor> for <person>`` when the caller names an agent."""
    if actor is None or actor == person:
        return person
    if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= _ACTOR_LIMIT:
        raise _Refused(400, "request", f"an actor name has 1 to {_ACTOR_LIMIT} characters")
    return f"{actor.strip()} for {person}"


def _cookie(secret: str, seconds: int, name: str = SESSION_COOKIE) -> str:
    """A cookie of the sign-in: only for this server over HTTPS, never for a script, and not sent by other sites'
    posts. The session cookie by default."""
    return f"{name}={secret}; Max-Age={seconds}; Path=/; Secure; HttpOnly; SameSite=Lax"


def _origin(url: str) -> str:
    """The origin of ``url`` as a browser sends it in ``Origin``: the host in lower case, no default port."""
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
    port = "" if parts.port in (None, {"http": 80, "https": 443}.get(parts.scheme)) else f":{parts.port}"
    return f"{parts.scheme}://{host}{port}"


def make_handler(store: SqlStore, log: bool = True, logins: Logins | None = None,
                 local_person: str | None = None) -> type[BaseHTTPRequestHandler]:
    """The request handler. ``local_person`` turns on local mode: the workspace page, with that person as author."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "taskman"
        # A caller that stops sending must not hold a thread.
        timeout = 30

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # The request line and the status go to stderr. A token is in a header, so it is not logged.
            if log:
                super().log_message(format, *args)

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            # Google returns the sign-in code and the state in the query of an /auth/ path: log only the path.
            url = urlsplit(getattr(self, "path", ""))
            if url.path.startswith("/auth/") and url.query:
                self.requestline = f"{self.command} {url.path} {self.request_version}"
            super().log_request(code, size)

        def _send(self, status: int, data: dict[str, Any], cookie: str | None = None) -> None:
            body = json.dumps(data, default=str, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)

        def _answer(self, respond: Callable[[], tuple[int, dict[str, Any]]]) -> None:
            """Send what ``respond`` returns, or the error it raises. No error leaves a partial change."""
            try:
                status, data = respond()
            except _Refused as exc:
                error = {"kind": exc.kind, "message": exc.message}
                if exc.usage:
                    error["usage"] = True
                status, data = exc.status, {"error": error}
            except Exception:  # noqa: BLE001 - the caller always gets JSON, never a traceback
                status, data = 500, {"error": {"kind": "server", "message": "The Taskman server failed."}}
            self._send(status, data)

        def _token(self) -> str:
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            return token.strip() if scheme == "Bearer" else ""

        def _person(self) -> str:
            token = self._token()
            person = person_for_token(store, token) if token else None
            if person is None:
                raise _Refused(401, "token", "a valid token is required")
            return person

        def _raw_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise _Refused(413 if length > MAX_REQUEST_BYTES else 400, "request", "invalid request size")
            return self.rfile.read(length)

        def _body(self) -> dict[str, Any]:
            raw = self._raw_body()
            try:
                data = json.loads(raw)
            except ValueError:
                raise _Refused(400, "request", "the request body must be JSON") from None
            if not isinstance(data, dict):
                raise _Refused(400, "request", "the request body must be an object")
            return data

        # ---- browser login ---------------------------------------------

        def _send_page(self, status: int, text: str, location: str | None = None, cookie: str | None = None) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # The pages carry one-time codes and tokens: no other site may frame them or see their address.
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _login_page(self, path: str, query: dict[str, str]) -> None:
            try:
                if path == "/auth/token":
                    # A person makes tokens on Settings > Tokens. An old link opens it, through sign-in when needed.
                    self._send_page(302, "", "/settings/tokens")
                    return
                if logins is None:
                    raise LoginError(404, "Browser login is not set up on this server. Use a personal access token.")
                if path in ("/auth/cli", "/auth/ui"):
                    address, browser = logins.start(path[len("/auth/"):], query, self._cookie_value(LOGIN_COOKIE))
                    # The workspace page's sign-in cookie: only this browser can finish the sign-in.
                    cookie = _cookie(browser, int(LOGIN_TIME.total_seconds()), LOGIN_COOKIE) if browser else None
                    self._send_page(302, "", address, cookie)
                elif path == "/auth/callback":
                    kind, *result = logins.finish(query, self._cookie_value(LOGIN_COOKIE))
                    if kind == "redirect":
                        self._send_page(302, "", result[0])
                    else:
                        return_path, secret = result
                        self._send_page(302, "", return_path, _cookie(secret, int(SESSION_TIME.total_seconds())))
                elif path == "/auth/signed-out":
                    self._send_page(200, signed_out_page())
                else:
                    raise LoginError(404, f"Not found: {path}")
            except LoginError as exc:
                self._send_page(exc.status, (ui_error_page if exc.kind == "ui" else error_page)(exc.message))
            except Exception:  # noqa: BLE001 - the browser gets a page, never a traceback
                self._send_page(500, error_page("The Taskman server failed."))

        def _exchange(self) -> tuple[int, dict[str, Any]]:
            if logins is None:
                raise _Refused(404, "missing", "Browser login is not set up on this server.")
            try:
                return 200, logins.exchange(self._body())
            except LoginError as exc:
                raise _Refused(exc.status, "login", exc.message) from None

        # ---- the workspace page with sign-in (hosted mode) ---------------

        def _cookie_value(self, cookie: str) -> str:
            """The value of a cookie of the request, or ``""``."""
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == cookie:
                    return value
            return ""

        def _session(self) -> str:
            """The session secret of the request's cookie, or ``""``."""
            return self._cookie_value(SESSION_COOKIE)

        def _refused(self, person: str | None, secret: str) -> bool:
            """Refuse a data call without a valid session (401), and a change that does not come from the
            server's own origin with the page's key (403). Checked before any change runs."""
            if person is None:
                # A data call cannot follow a sign-in: the page reloads itself on 401.
                status, kind, message = 401, "session", "Your Taskman session ended. Sign in again."
            elif self.command == "POST" and (
                    self.headers.get("Origin") != _origin(logins.settings.public_url)
                    or not self.headers.get("X-Taskman-CSRF")):
                status, kind, message = 403, "csrf", "This change does not come from a Taskman page of this server."
            elif self.command == "POST" and not hmac.compare_digest(self.headers.get("X-Taskman-CSRF", "").encode(),
                                                                    csrf_token(secret).encode()):
                # A page of this server with the key of an earlier session, such as a second tab after a new
                # sign-in in the first: the page reloads itself and gets the key of the current session.
                status, kind, message = 403, "stale", "This page is from an earlier sign-in. Reload the page."
            else:
                return False
            self._send(status, {"error": {"kind": kind, "message": message}})
            return True

        def _hosted(self) -> None:
            """A request of the workspace page in hosted mode: the page, a data call, or a change."""
            url = urlsplit(self.path)
            secret = self._session()
            try:
                person = logins.session_person(secret)
            except Exception:  # noqa: BLE001 - the page gets JSON, never a traceback
                self._send(500, {"error": {"kind": "server", "message": "The Taskman server failed."}})
                return
            if self.command == "GET" and not url.path.startswith("/api/"):
                if person is None:
                    # Google signs the person in, then the browser returns to this address.
                    self._login_page("/auth/ui", {"path": self.path})
                else:
                    workspace.respond(self, store, person, logins.settings.public_url, csrf_token(secret))
            elif not self._refused(person, secret):
                workspace.respond(self, store, person, logins.settings.public_url)

        def _logout(self) -> None:
            """Sign out of the workspace page: end the session and clear its cookie."""
            if logins is None or local_person:
                self._send(404, {"error": {"kind": "missing",
                                           "message": "Browser login is not set up on this server."}})
                return
            secret = self._session()
            try:
                if self._refused(logins.session_person(secret), secret):
                    return
                logins.end_session(secret)
            except Exception:  # noqa: BLE001 - the page gets JSON, never a traceback
                self._send(500, {"error": {"kind": "server", "message": "The Taskman server failed."}})
                return
            self._send(200, {}, _cookie("", 0))

        # ---- GET -------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            if workspace.is_page_request("GET", url.path):
                if local_person:
                    workspace.answer(self, store, local_person)
                    return
                if logins:
                    self._hosted()
                    return
                if not url.path.startswith("/api/"):
                    self._send_page(200, ui_not_set_up_page())
                    return
            if url.path.startswith("/auth/"):
                self._login_page(url.path, dict(parse_qsl(url.query)))
                return
            self._answer(self._get)

        def _get(self) -> tuple[int, dict[str, Any]]:
            path = urlsplit(self.path).path
            if path == "/healthz":
                return 200, {"ok": True}
            if path == "/readyz":
                try:
                    store.projects()
                except Exception:  # noqa: BLE001 - any database failure means not ready
                    raise _Refused(503, "server", "The database is not reachable.") from None
                return 200, {"ok": True}
            if not path.startswith(API + "/"):
                raise _Refused(404, "missing", f"Not found: {path}")
            person = self._person()
            if path == API + "/whoami":
                return 200, {"person": person}
            if path == API + "/tokens":
                return 200, {"tokens": own_tokens(store, person)}
            parts = [unquote(part) for part in path[len(API) + 1:].split("/")]
            # Readers share the write lock so one answer never mixes two states.
            with store.lock():
                return 200, self._read(parts, path)

        def _read(self, parts: list[str], path: str) -> dict[str, Any]:
            if parts == ["items"]:
                return {"items": [_public(item) for item in store.items()]}
            if parts == ["order"]:
                return {"order": store.sibling_order()}
            if parts == ["projects"]:
                return {"projects": store.projects()}
            if len(parts) == 2 and parts[0] == "items":
                item = store.get(parts[1])
                return {"item": _public(item) if item else None}
            if len(parts) == 3 and parts[0] == "items" and parts[2] in ("document", "activity"):
                try:
                    if parts[2] == "document":
                        return {"document": store.document(parts[1])}
                    return {"activity": store.activity(parts[1])}
                except KeyError:
                    raise _Refused(404, "missing", parts[1]) from None
                except StorageError as exc:
                    raise _Refused(409, "storage", str(exc)) from None
            raise _Refused(404, "missing", f"Not found: {path}")

        # ---- POST ------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if local_person and workspace.is_page_request("POST", path):
                workspace.answer(self, store, local_person)
                return
            if logins and workspace.is_page_request("POST", path):
                self._hosted()
                return
            if path == "/auth/logout":
                self._logout()
                return
            self._answer(self._post)

        def _post(self) -> tuple[int, dict[str, Any]]:
            path = urlsplit(self.path).path
            if path == "/auth/exchange":
                return self._exchange()
            if path == API + "/logout":
                self._person()
                revoke_own_token(store, self._token())
                return 200, {}
            revoke = _TOKEN_REVOKE.match(path)
            if revoke:
                # Another person's token counts as no token, so its ID tells the caller nothing.
                token_id = int(revoke.group(1))
                if not revoke_token(store, token_id, self._person()):
                    raise _Refused(404, "missing", f"no active token of yours has the ID {token_id}")
                return 200, {"revoked": token_id}
            prefix = API + "/operations/"
            if not path.startswith(prefix):
                raise _Refused(404, "missing", f"Not found: {path}")
            person = self._person()
            operation = OPERATIONS.get(path[len(prefix):])
            if operation is None:
                raise _Refused(404, "missing", f"Unknown operation: {path[len(prefix):]}")
            data = self._body()
            arguments = _arguments(operation, data.get("arguments", {}))
            # A comment names its author as an argument. That name is the agent, never the person.
            author = author_name(person, data.get("actor") or arguments.get("actor") or None)
            if "actor" in arguments:
                arguments["actor"] = author
            try:
                with store.lock():
                    store.actor = author
                    result = operation(store, **arguments)
            except lifecycle.NoChange as exc:
                return 200, {"no_change": exc.message}
            except lifecycle.LifecycleError as exc:
                raise _Refused(422, "lifecycle", exc.message, exc.usage) from None
            except StorageError as exc:
                raise _Refused(409, "storage", str(exc)) from None
            except ValueError as exc:
                raise _Refused(422, "value", str(exc)) from None
            if isinstance(result, Item):
                return 200, {"item": _public(result)}
            return 200, {} if result is None else {"order": result}

    return Handler


def make_server(host: str, port: int, store: SqlStore, log: bool = True,
                logins: Logins | None = None, local_person: str | None = None) -> ThreadingHTTPServer:
    """A server for ``store``, with browser login when ``logins`` is given and local mode when ``local_person``
    is given. The caller runs ``serve_forever``."""
    server = ThreadingHTTPServer((host, port), make_handler(store, log, logins, local_person))
    server.daemon_threads = True
    return server
