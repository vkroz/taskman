"""Taskman server: the HTTP API that the CLI calls.

The server alone opens the database. A caller proves who they are with a
token (``Authorization: Bearer ...``); the person of the token is the author
of every change. Changes arrive as whole operations and run here, under the
store lock, so the lifecycle rules are checked next to the data.
"""
from __future__ import annotations

import inspect
import json
import types
import typing
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from taskman import lifecycle
from taskman.model.ordering import reorder_item
from taskman.login import LoginError, Logins, error_page
from taskman.storage.accounts import person_for_token, revoke_own_token
from taskman.storage.sql_store import SqlStore
from taskman.storage.store import Item, StorageError, item_data

API = "/api/v1"
MAX_REQUEST_BYTES = 1024 * 1024
_ACTOR_LIMIT = 35

OPERATIONS: dict[str, Callable] = {
    operation.__name__: operation
    for operation in (
        lifecycle.create, lifecycle.finalize, lifecycle.update_body, lifecycle.record,
        lifecycle.set_dependencies, lifecycle.discard, lifecycle.move, lifecycle.convert,
        lifecycle.close, lifecycle.edit, lifecycle.assign, lifecycle.label, reorder_item,
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


def author_name(person: str, actor: Any) -> str:
    """The author of a change: the person, or ``<actor> for <person>`` when the caller names an agent."""
    if actor is None or actor == person:
        return person
    if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= _ACTOR_LIMIT:
        raise _Refused(400, "request", f"an actor name has 1 to {_ACTOR_LIMIT} characters")
    return f"{actor.strip()} for {person}"


def make_handler(store: SqlStore, log: bool = True, logins: Logins | None = None) -> type[BaseHTTPRequestHandler]:

    class Handler(BaseHTTPRequestHandler):
        server_version = "taskman"
        # A caller that stops sending must not hold a thread.
        timeout = 30

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # The request line and the status go to stderr. A token is in a header, so it is not logged.
            if log:
                super().log_message(format, *args)

        def _send(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, default=str, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
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

        def _body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise _Refused(413 if length > MAX_REQUEST_BYTES else 400, "request", "invalid request size")
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                raise _Refused(400, "request", "the request body must be JSON") from None
            if not isinstance(data, dict):
                raise _Refused(400, "request", "the request body must be an object")
            return data

        # ---- browser login ---------------------------------------------

        def _send_page(self, status: int, text: str, location: str | None = None) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            if location:
                self.send_header("Location", location)
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
                if logins is None:
                    raise LoginError(404, "Browser login is not set up on this server. Use a personal access token.")
                if path in ("/auth/cli", "/auth/token"):
                    self._send_page(302, "", logins.start(path[len("/auth/"):], query))
                elif path == "/auth/callback":
                    kind, result = logins.finish(query)
                    if kind == "redirect":
                        self._send_page(302, "", result)
                    else:
                        self._send_page(200, result)
                else:
                    raise LoginError(404, f"Not found: {path}")
            except LoginError as exc:
                self._send_page(exc.status, error_page(exc.message))
            except Exception:  # noqa: BLE001 - the browser gets a page, never a traceback
                self._send_page(500, error_page("The Taskman server failed."))

        def _exchange(self) -> tuple[int, dict[str, Any]]:
            if logins is None:
                raise _Refused(404, "missing", "Browser login is not set up on this server.")
            try:
                return 200, logins.exchange(self._body())
            except LoginError as exc:
                raise _Refused(exc.status, "login", exc.message) from None

        # ---- GET -------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
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
            self._answer(self._post)

        def _post(self) -> tuple[int, dict[str, Any]]:
            path = urlsplit(self.path).path
            if path == "/auth/exchange":
                return self._exchange()
            if path == API + "/logout":
                self._person()
                revoke_own_token(store, self._token())
                return 200, {}
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
                logins: Logins | None = None) -> ThreadingHTTPServer:
    """A server for ``store``, with browser login when ``logins`` is given. The caller runs ``serve_forever``."""
    server = ThreadingHTTPServer((host, port), make_handler(store, log, logins))
    server.daemon_threads = True
    return server
