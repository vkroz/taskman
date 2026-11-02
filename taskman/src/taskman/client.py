"""HTTP client used by the taskman CLI. The server is the system of record."""
from __future__ import annotations

import json
from urllib import error, request
from urllib.parse import urljoin, urlparse

from taskman.config import resolve_settings


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _endpoint(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, (parsed.hostname or "").lower(), port


class _SameHostRedirect(request.HTTPRedirectHandler):
    """Follow redirects only when the host does not change.

    urllib would otherwise copy the Authorization header onto a redirect to
    another host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        if _endpoint(req.full_url) != _endpoint(target):
            raise ApiError("Refusing to follow a redirect that would send the access token to another host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, base_url: str, token: str, timeout: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict | None = None, *, auth: bool = True):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise ApiError(
                    "TASKMAN_ACCESS_TOKEN is not set. Export it, or put token in the user Taskman config file."
                )
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(self.base_url + path, data=data, headers=headers, method=method)
        opener = request.build_opener(_SameHostRedirect)
        try:
            with opener.open(req, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except ApiError:
            raise
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


def client_from_env(*, timeout: float = 30) -> Client:
    settings = resolve_settings()
    return Client(settings.url, settings.token, timeout=timeout)
