"""Browser login with Google, on the Taskman server.

``taskman auth login`` opens ``/auth/cli`` in the browser. The server sends the
person to Google. Google sends the person back to ``/auth/callback`` with a
code. The server exchanges that code with Google for the person's verified
email address, then sends the browser to the CLI's listener on 127.0.0.1 with a
one-time exchange code. The CLI trades that code at ``/auth/exchange`` for a
token. The CLI proves with a secret of its own (PKCE) that it started the login,
so a stolen exchange code is useless.

``/auth/token`` signs a person in the same way and shows a personal access
token on the page, for a machine with no browser.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from taskman.storage.accounts import create_token
from taskman.storage.sql_store import SqlStore
from taskman.storage.store import StorageConfigError

TOKEN_DAYS = 90
LOGIN_TIME = timedelta(minutes=10)
EXCHANGE_TIME = timedelta(minutes=2)
_STATE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True, slots=True)
class Provider:
    """The OpenID Connect endpoints of the sign-in provider. The defaults are Google's."""

    authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url: str = "https://oauth2.googleapis.com/token"
    issuers: tuple[str, ...] = ("https://accounts.google.com", "accounts.google.com")


@dataclass(frozen=True, slots=True)
class LoginSettings:
    client_id: str
    client_secret: str = ""
    public_url: str = ""
    emails: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    provider: Provider = Provider()

    def allows(self, email: str) -> bool:
        return email in self.emails or email.rpartition("@")[2] in self.domains


def _names(value: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in value.split(",") if part.strip())


def login_settings(environ: Mapping[str, str] = os.environ) -> LoginSettings | None:
    """Browser login settings, or ``None`` when ``TASKMAN_GOOGLE_CLIENT_ID`` is not set."""
    client_id = environ.get("TASKMAN_GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        return None
    direct = environ.get("TASKMAN_GOOGLE_CLIENT_SECRET", "").strip()
    file_name = environ.get("TASKMAN_GOOGLE_CLIENT_SECRET_FILE", "").strip()
    if direct and file_name:
        raise StorageConfigError("Set TASKMAN_GOOGLE_CLIENT_SECRET or TASKMAN_GOOGLE_CLIENT_SECRET_FILE, not both.")
    secret = direct
    if file_name:
        try:
            secret = Path(file_name).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StorageConfigError(
                f"Cannot read the file named by TASKMAN_GOOGLE_CLIENT_SECRET_FILE: {exc.strerror}."
            ) from None
    if not secret:
        raise StorageConfigError("Browser login needs TASKMAN_GOOGLE_CLIENT_SECRET or TASKMAN_GOOGLE_CLIENT_SECRET_FILE.")
    public_url = environ.get("TASKMAN_PUBLIC_URL", "").strip().rstrip("/")
    parts = urlsplit(public_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise StorageConfigError(
            "Browser login needs TASKMAN_PUBLIC_URL: the address that people open, for example https://taskman.example.com."
        )
    emails, domains = _names(environ.get("TASKMAN_ALLOWED_EMAILS", "")), _names(environ.get("TASKMAN_ALLOWED_DOMAINS", ""))
    if not emails and not domains:
        raise StorageConfigError("Browser login needs TASKMAN_ALLOWED_EMAILS or TASKMAN_ALLOWED_DOMAINS.")
    return LoginSettings(client_id, secret, public_url, emails, domains)


class LoginError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def challenge_for(verifier: str) -> str:
    """The PKCE S256 challenge of a verifier (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claims(id_token: str) -> dict[str, Any]:
    """The claims of an ID token.

    The server receives the ID token straight from the provider's token
    endpoint over HTTPS, so the TLS connection proves the issuer and the
    signature check is not needed (OpenID Connect Core 1.0, section 3.1.3.7).
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise LoginError(502, "Google returned an ID token that Taskman cannot read.") from None
    if not isinstance(claims, dict):
        raise LoginError(502, "Google returned an ID token that Taskman cannot read.")
    return claims


def _post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=urlencode(form).encode("ascii"), method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise LoginError(502, f"Google refused the sign-in code (HTTP status {exc.code}).") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise LoginError(502, "Taskman cannot reach Google to finish the sign-in.") from None


class Logins:
    """The login steps. Each step keeps its state in the ``logins`` table, so any server instance can serve it."""

    def __init__(self, store: SqlStore, settings: LoginSettings,
                 post_form: Callable[[str, dict[str, str]], dict[str, Any]] = _post_form) -> None:
        self.store, self.settings, self._post_form = store, settings, post_form

    def _q(self, statement: str, params: tuple = ()) -> Any:
        return self.store.conn.execute(self.store.dialect.sql(statement), params)

    @property
    def _redirect_uri(self) -> str:
        return f"{self.settings.public_url}/auth/callback"

    def start(self, kind: str, query: dict[str, str]) -> str:
        """Record a new login and return the provider's sign-in address."""
        label = query.get("label", "").strip()
        if len(label) > 60:
            raise LoginError(400, "A label has 60 characters or fewer.")
        port = cli_state = challenge = None
        if kind == "cli":
            try:
                port = int(query.get("port", ""))
            except ValueError:
                port = 0
            if not 1024 <= port <= 65535:
                raise LoginError(400, "This login address is not valid: the port is missing or wrong.")
            cli_state, challenge = query.get("state", ""), query.get("challenge", "")
            if not _STATE.match(cli_state) or not _CHALLENGE.match(challenge):
                raise LoginError(400, "This login address is not valid. Run `taskman auth login` again.")
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = _now()
        with self.store.lock():
            self._q("DELETE FROM logins WHERE created_at < ?", ((now - timedelta(days=1)).isoformat(),))
            self._q(
                "INSERT INTO logins (state_hash, kind, cli_port, cli_state, code_challenge, label, nonce, verifier, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_fingerprint(state), kind, port, cli_state, challenge, label, nonce, verifier, now.isoformat()),
            )
        return self.settings.provider.authorize_url + "?" + urlencode({
            "client_id": self.settings.client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge_for(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })

    def finish(self, query: dict[str, str]) -> tuple[str, str]:
        """Handle the provider's return. Gives ``("redirect", url)`` for the CLI, or ``("token", html)``."""
        state = query.get("state", "")
        now = _now()
        with self.store.lock():
            row = self._q(
                "SELECT id, kind, cli_port, cli_state, label, nonce, verifier, created_at FROM logins "
                "WHERE state_hash = ? AND finished_at IS NULL",
                (_fingerprint(state),),
            ).fetchone()
            if row is None:
                raise LoginError(400, "This sign-in is unknown or already used. Start the login again.")
            # One use only, also when a later check fails.
            self._q("UPDATE logins SET finished_at = ? WHERE id = ?", (now.isoformat(), row[0]))
        login_id, kind, port, cli_state, label, nonce, verifier, created_at = row
        if now - datetime.fromisoformat(created_at) > LOGIN_TIME:
            raise LoginError(400, "This sign-in took more than 10 minutes. Start the login again.")
        if query.get("error"):
            raise LoginError(400, f"Google did not sign you in: {query['error']}.")
        if not query.get("code"):
            raise LoginError(400, "Google returned no sign-in code. Start the login again.")
        answer = self._post_form(self.settings.provider.token_url, {
            "grant_type": "authorization_code",
            "code": query["code"],
            "redirect_uri": self._redirect_uri,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "code_verifier": verifier,
        })
        email = self._verified_email(_claims(str(answer.get("id_token", ""))), nonce)
        if kind == "token":
            _, token = create_token(self.store, email, label or "web page", TOKEN_DAYS)
            return "token", token_page(email, token)
        code = secrets.token_urlsafe(32)
        with self.store.lock():
            self._q("UPDATE logins SET person = ?, exchange_hash = ? WHERE id = ?", (email, _fingerprint(code), login_id))
        return "redirect", f"http://127.0.0.1:{port}/callback?" + urlencode({"code": code, "state": cli_state})

    def _verified_email(self, claims: dict[str, Any], nonce: str) -> str:
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        expires = claims.get("exp")
        if (claims.get("iss") not in self.settings.provider.issuers or self.settings.client_id not in audiences
                or not isinstance(expires, (int, float)) or expires <= _now().timestamp()
                or claims.get("nonce") != nonce):
            raise LoginError(502, "Google returned an ID token that is not for this login.")
        email = str(claims.get("email") or "").strip().lower()
        if not email or claims.get("email_verified") not in (True, "true"):
            raise LoginError(403, "Google did not confirm an email address for this account.")
        if not self.settings.allows(email):
            raise LoginError(403, f"{email} may not use this Taskman server. Ask its operator to add the address.")
        return email

    def exchange(self, data: dict[str, Any]) -> dict[str, str]:
        """Trade the one-time exchange code and the CLI's verifier for a token."""
        code, verifier = data.get("code"), data.get("verifier")
        if not isinstance(code, str) or not isinstance(verifier, str) or not code or not verifier:
            raise LoginError(400, "The exchange needs a code and a verifier.")
        now = _now()
        with self.store.lock():
            row = self._q(
                "SELECT id, code_challenge, label, person, finished_at FROM logins "
                "WHERE exchange_hash = ? AND exchanged_at IS NULL",
                (_fingerprint(code),),
            ).fetchone()
            if row is None:
                raise LoginError(400, "This login code is unknown or already used. Run `taskman auth login` again.")
            self._q("UPDATE logins SET exchanged_at = ? WHERE id = ?", (now.isoformat(), row[0]))
        _, challenge, label, person, finished_at = row
        if now - datetime.fromisoformat(finished_at) > EXCHANGE_TIME:
            raise LoginError(400, "This login code expired. Run `taskman auth login` again.")
        if not secrets.compare_digest(challenge_for(verifier), challenge):
            raise LoginError(400, "This login was started by another program. Run `taskman auth login` again.")
        _, token = create_token(self.store, person, label, TOKEN_DAYS)
        return {"token": token, "person": person}


def page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#1f2328}"
        "code{display:block;padding:.75rem;background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;"
        "word-break:break-all;font-size:15px}"
        "</style></head><body>" + body + "</body></html>"
    )


def token_page(email: str, token: str) -> str:
    return page("Taskman token", (
        f"<h1>Your Taskman token</h1><p>Signed in as {html.escape(email)}.</p>"
        f"<code>{html.escape(token)}</code>"
        "<p>Copy it now. This page does not show it again. It expires in 90 days.</p>"
        "<p>On the machine without a browser, run <b>taskman auth login --with-token</b> and paste the token.</p>"
    ))


def error_page(message: str) -> str:
    return page("Taskman login failed", f"<h1>Login failed</h1><p>{html.escape(message)}</p>")
