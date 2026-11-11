"""Remote store: the CLI reads and changes work items through a Taskman server.

Reads map to GET requests. Changes go to the server as whole operations
(``taskman.lifecycle``), so the server checks every rule against its own
database. The CLI never learns which database the server uses.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote, urlsplit

from taskman.storage.store import Item, StorageConfigError, StorageError, item_from_data

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_NO_RAW_WRITES = "A Taskman server accepts changes as operations only, not as direct writes."


def server_url(environ: Mapping[str, str] = os.environ) -> str:
    """The server named by ``TASKMAN_SERVER_URL``, or an empty string for direct database use."""
    return environ.get("TASKMAN_SERVER_URL", "").strip().rstrip("/")


def remote_settings(environ: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """The server URL and the token. A token travels over HTTPS, or to this machine only."""
    url = server_url(environ)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise StorageConfigError("TASKMAN_SERVER_URL must be an http:// or https:// address.")
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
        raise StorageConfigError("TASKMAN_SERVER_URL must use https:// unless the server runs on this machine.")
    token = environ.get("TASKMAN_TOKEN", "").strip()
    if not token:
        raise StorageConfigError(f"TASKMAN_TOKEN is not set. Taskman needs a token for {url}.")
    return url, token


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the token to another address, so none is followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StorageConfigError(f"The Taskman server redirected to {newurl}. Taskman does not follow redirects.")


def open_remote_store(environ: Mapping[str, str] = os.environ) -> RemoteStore:
    return RemoteStore(*remote_settings(environ))


class RemoteStore:
    def __init__(self, url: str, token: str, timeout: float = 30) -> None:
        self.url = url
        self._actor: str | None = None
        self._person: str | None = None
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    @property
    def actor(self) -> str:
        """The ``--actor`` value, or the person of the token when the caller names no actor."""
        if self._actor:
            return self._actor
        if self._person is None:
            self._person = self._request("GET", "/api/v1/whoami")["person"]
        return self._person

    @actor.setter
    def actor(self, name: str) -> None:
        self._actor = name

    def close(self) -> None:
        return

    @contextmanager
    def lock(self) -> Iterator[None]:
        """The server runs each operation in one transaction; the CLI holds no lock."""
        yield

    # ---- requests ------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=body, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return json.loads(response.read())
        except StorageConfigError:
            raise
        except urllib.error.HTTPError as exc:
            _raise_reported(exc.code, exc.read(), self.url)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise StorageConfigError(f"Cannot reach the Taskman server at {self.url}: {reason}") from None
        except ValueError:
            raise StorageConfigError(f"{self.url} did not answer as a Taskman server.") from None

    def forward(self, operation: str, arguments: dict[str, Any]) -> Any:
        """Run one operation on the server and return what it returns locally."""
        answer = self._request(
            "POST", f"/api/v1/operations/{operation}", {"actor": self._actor, "arguments": arguments}
        )
        if "no_change" in answer:
            from taskman.lifecycle import NoChange

            raise NoChange(answer["no_change"])
        if "item" in answer:
            return item_from_data(answer["item"])
        return answer.get("order")

    # ---- reads ---------------------------------------------------------

    def items(self) -> list[Item]:
        return [item_from_data(data) for data in self._request("GET", "/api/v1/items")["items"]]

    def get(self, item_id: str) -> Item | None:
        data = self._request("GET", f"/api/v1/items/{quote(item_id, safe='')}")["item"]
        return item_from_data(data) if data else None

    def document(self, item_id: str) -> str:
        return self._request("GET", f"/api/v1/items/{quote(item_id, safe='')}/document")["document"]

    def activity(self, item_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/items/{quote(item_id, safe='')}/activity")["activity"]

    def sibling_order(self) -> dict[str, list[str]]:
        return self._request("GET", "/api/v1/order")["order"]

    def projects(self) -> list[dict[str, str]]:
        return self._request("GET", "/api/v1/projects")["projects"]

    # ---- direct writes: not offered by a server ------------------------

    def create(self, **_: Any) -> Item:
        raise StorageError(_NO_RAW_WRITES)

    def save(self, item_id: str, metadata: dict[str, Any], body: str) -> Item:
        raise StorageError(_NO_RAW_WRITES)

    def set_status(self, item_id: str, status: str) -> Item:
        raise StorageError(_NO_RAW_WRITES)

    def set_type(self, item_id: str, item_type: str) -> Item:
        raise StorageError(_NO_RAW_WRITES)

    def delete(self, item_id: str) -> None:
        raise StorageError(_NO_RAW_WRITES)

    def save_order(self, parent_key: str, item_ids: list[str], moved_id: str) -> None:
        raise StorageError(_NO_RAW_WRITES)


def _raise_reported(status: int, body: bytes, url: str) -> None:
    """Raise the error that the same operation raises against a local database."""
    from taskman.lifecycle import LifecycleError

    try:
        error = json.loads(body)["error"]
        kind, message = error["kind"], error["message"]
    except (ValueError, KeyError, TypeError):
        raise StorageConfigError(f"{url} answered with HTTP status {status}.") from None
    if status == 401:
        raise StorageConfigError(f"The Taskman server at {url} refused the token: {message}")
    if kind == "lifecycle":
        raise LifecycleError(message, usage=bool(error.get("usage")))
    if kind == "missing":
        raise KeyError(message)
    if kind == "value":
        raise ValueError(message)
    raise StorageError(message)
