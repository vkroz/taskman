"""HTTP client used by the taskman CLI. The server is the system of record."""
from __future__ import annotations

import json
from urllib import error, request

from taskman.config import resolve_settings


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Client:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict | None = None, *, auth: bool = True):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise ApiError(
                    "TASKMAN_ACCESS_TOKEN is not set. Export it, or put token in the Taskman config file."
                )
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(body).get("error") or body
            except ValueError:
                message = body or str(exc.reason)
            raise ApiError(str(message), exc.code) from exc
        except error.URLError as exc:
            raise ApiError(f"cannot reach {self.base_url}: {exc.reason}") from exc

    def health(self) -> dict:
        return self.request("GET", "/health", auth=False)


def client_from_env() -> Client:
    settings = resolve_settings()
    return Client(settings.url, settings.token)
