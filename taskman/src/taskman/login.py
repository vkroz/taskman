"""Browser login with Google, on the Taskman server.

``taskman auth login`` opens ``/auth/cli`` in the browser. The server sends the
person to Google. Google sends the person back to ``/auth/callback`` with a
code. The server exchanges that code with Google for the person's verified
email address, then sends the browser to the CLI's listener on 127.0.0.1 with a
one-time exchange code. The CLI trades that code at ``/auth/exchange`` for a
token. The CLI proves with a secret of its own (PKCE) that it started the login,
so a stolen exchange code is useless.

A person makes personal access tokens, for a machine with no browser, on
the Settings > Tokens screen of the workspace page. ``/auth/token`` sends the
browser there.

On a hosted server the workspace page signs a person in the same way. After
``/auth/callback``, the server sets a session cookie and sends the browser
back to the page address that the person opened. The database keeps only a
fingerprint of the session secret, so every server instance accepts the
session. A session ends 7 days after the sign-in, or at sign-out. A sign-in
cookie binds the sign-in to the browser that started it (RFC 6749, section
10.12), so a person cannot send the callback address of their own sign-in to
another person and make that browser work as them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.client
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
# How long the logins table keeps a row without and with a signed-in person. Both are well
# above LOGIN_TIME + EXCHANGE_TIME, so the cleanup never removes a row that a running login still needs.
ANONYMOUS_KEPT = timedelta(hours=1)
SIGNED_IN_KEPT = timedelta(days=1)
# A browser session of the workspace page. Use does not renew it.
SESSION_TIME = timedelta(days=7)
SESSION_COOKIE = "__Host-taskman_session"
LOGIN_COOKIE = "__Host-taskman_login"
_SECRET = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
# A page address of this server: one "/" and no second "/" or "\\" after it, so never another host.
_RETURN_PATH = re.compile(r"^/(?![/\\])[!-~]{0,2000}$")
_STATE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ERROR_CODE = re.compile(r"^[a-z_]{1,64}$")


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

    def allows(self, email: str, workspace_domain: str) -> bool:
        """A listed email address, or a member of a listed Google Workspace organization."""
        return email in self.emails or workspace_domain in self.domains


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
    # The sign-in cookies are Secure: a browser keeps them over plain HTTP only on this machine.
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise StorageConfigError("TASKMAN_PUBLIC_URL must use https:// unless the server runs on this machine.")
    emails, domains = _names(environ.get("TASKMAN_ALLOWED_EMAILS", "")), _names(environ.get("TASKMAN_ALLOWED_DOMAINS", ""))
    if not emails and not domains:
        raise StorageConfigError("Browser login needs TASKMAN_ALLOWED_EMAILS or TASKMAN_ALLOWED_DOMAINS.")
    return LoginSettings(client_id, secret, public_url, emails, domains)


class LoginError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message
        # The kind of the login that failed, when the server knows it.
        self.kind = ""


def challenge_for(verifier: str) -> str:
    """The PKCE S256 challenge of a verifier (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def csrf_token(secret: str) -> str:
    """The key that the workspace page sends with each change. Only a holder of the session secret can make it."""
    return hmac.new(secret.encode("ascii"), b"taskman csrf", hashlib.sha256).hexdigest()


def _return_path(value: str) -> str:
    """The page address to return to after a sign-in: ``value`` when it is a path of this server, else ``/``."""
    return value if _RETURN_PATH.fullmatch(value) and "\\" not in value else "/"


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


def _error_code(exc: urllib.error.HTTPError) -> str:
    """The OAuth error code of a refused request (RFC 6749, section 5.2), such as ``invalid_client``, or ``""``.

    Only the code: ``error_description`` is free text of the provider.
    """
    try:
        answer = json.loads(exc.read(4096))
    except (OSError, ValueError, http.client.HTTPException):
        return ""
    code = answer.get("error") if isinstance(answer, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else ""


def _post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=urlencode(form).encode("ascii"), method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        code = _error_code(exc)
        detail = f"HTTP status {exc.code}, {code}" if code else f"HTTP status {exc.code}"
        raise LoginError(502, f"Google refused the sign-in ({detail}).") from None
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

    def start(self, kind: str, query: dict[str, str], browser: str = "") -> tuple[str, str]:
        """Record a new login. Gives the provider's sign-in address and, for the workspace page, the key of the
        browser's sign-in cookie: ``browser`` when it is a valid key, so that two tabs can sign in at once."""
        label = query.get("label", "").strip()
        if len(label) > 60:
            raise LoginError(400, "A label has 60 characters or fewer.")
        port = cli_state = challenge = return_path = browser_hash = None
        if kind == "ui":
            return_path = _return_path(query.get("path", ""))
            browser = browser if _SECRET.match(browser) else secrets.token_urlsafe(32)
            browser_hash = _fingerprint(browser)
        else:
            browser = ""
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
            # Anyone can start a login, and end it with an error, without a token. Only a row where
            # Google signed in a person who may use this server has a person; other rows go after 1 hour.
            self._q(
                "DELETE FROM logins WHERE created_at < ? AND (person IS NULL OR created_at < ?)",
                ((now - ANONYMOUS_KEPT).isoformat(), (now - SIGNED_IN_KEPT).isoformat()),
            )
            self._q(
                "INSERT INTO logins (state_hash, kind, cli_port, cli_state, code_challenge, label, nonce, verifier, "
                "created_at, return_path, browser_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_fingerprint(state), kind, port, cli_state, challenge, label, nonce, verifier, now.isoformat(),
                 return_path, browser_hash),
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
        }), browser

    def finish(self, query: dict[str, str], browser: str = "") -> tuple[str, ...]:
        """Handle the provider's return. ``browser`` is the key of the browser's sign-in cookie. Gives
        ``("redirect", url)`` for the CLI, or ``("session", path, secret)`` for the workspace
        page: the page address and the secret of the new session."""
        state = query.get("state", "")
        now = _now()
        with self.store.lock():
            # A sign-in of the removed token page (kind 'token') counts as unknown.
            row = self._q(
                "SELECT id, kind, cli_port, cli_state, label, nonce, verifier, created_at, return_path, browser_hash, "
                "finished_at FROM logins WHERE state_hash = ? AND kind IN ('cli', 'ui')",
                (_fingerprint(state),),
            ).fetchone()
            if row is None or row[-1] is not None:
                error = LoginError(400, "This sign-in is unknown or already used. Start the login again.")
                # A reload of the callback address shows the page of the login's own kind.
                error.kind = row[1] if row else ""
                raise error
            # One use only, also when a later check fails.
            self._q("UPDATE logins SET finished_at = ? WHERE id = ?", (now.isoformat(), row[0]))
        try:
            return self._finish(row[:-1], query, now, browser)
        except LoginError as exc:
            exc.kind = row[1]
            raise

    def _finish(self, row: tuple, query: dict[str, str], now: datetime, browser: str) -> tuple[str, ...]:
        login_id, kind, port, cli_state, label, nonce, verifier, created_at, return_path, browser_hash = row
        if now - datetime.fromisoformat(created_at) > LOGIN_TIME:
            raise LoginError(400, "This sign-in took more than 10 minutes. Start the login again.")
        if kind == "ui" and not hmac.compare_digest(_fingerprint(browser), browser_hash or ""):
            raise LoginError(400, "This sign-in was started in another browser. Open Taskman again in this browser.")
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
        if kind == "ui":
            secret = secrets.token_urlsafe(32)
            with self.store.lock():
                self._q("UPDATE logins SET person = ? WHERE id = ?", (email, login_id))
                # Ended and expired sessions are never accepted again: remove them.
                self._q("DELETE FROM sessions WHERE ended_at IS NOT NULL OR expires_at < ?", (now.isoformat(),))
                self._q("INSERT INTO sessions (secret_hash, person, created_at, expires_at) VALUES (?, ?, ?, ?)",
                        (_fingerprint(secret), email, now.isoformat(), (now + SESSION_TIME).isoformat()))
            return "session", return_path or "/", secret
        code = secrets.token_urlsafe(32)
        with self.store.lock():
            self._q("UPDATE logins SET person = ?, exchange_hash = ? WHERE id = ?", (email, _fingerprint(code), login_id))
        return "redirect", f"http://127.0.0.1:{port}/callback?" + urlencode({"code": code, "state": cli_state})

    def session_person(self, secret: str) -> str | None:
        """The person of a session that has not expired or ended, or ``None``."""
        if not _SECRET.match(secret):
            return None
        with self.store.lock():
            row = self._q("SELECT person, expires_at, ended_at FROM sessions WHERE secret_hash = ?",
                          (_fingerprint(secret),)).fetchone()
        if row is None or row[2] or _now() >= datetime.fromisoformat(row[1]):
            return None
        return row[0]

    def end_session(self, secret: str) -> None:
        """End a session at sign-out. The next request with its cookie is not signed in."""
        with self.store.lock():
            self._q("UPDATE sessions SET ended_at = ? WHERE secret_hash = ? AND ended_at IS NULL",
                    (_now().isoformat(), _fingerprint(secret)))

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
        # Google sets "hd" only for an account that a Google Workspace organization manages.
        # An email address alone does not prove membership: a personal Google account can use any address.
        workspace_domain = str(claims.get("hd") or "").strip().lower()
        if not self.settings.allows(email, workspace_domain):
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
                "WHERE exchange_hash = ? AND kind = 'cli' AND exchanged_at IS NULL",
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
        "</style></head><body>" + body + "</body></html>"
    )


def ui_error_page(message: str) -> str:
    return page("Taskman sign-in failed", (
        f"<h1>Sign-in failed</h1><p>{html.escape(message)}</p><p><a href=\"/\">Open Taskman again</a></p>"
    ))


def signed_out_page() -> str:
    return page("Signed out of Taskman", (
        "<h1>You signed out</h1><p>Your Taskman session on this browser ended.</p>"
        "<p><a href=\"/\">Sign in again</a></p>"
    ))


def ui_not_set_up_page() -> str:
    return page("Taskman", (
        "<h1>Browser login is not set up</h1>"
        "<p>This Taskman server does not serve the workspace page: it has no Google sign-in settings.</p>"
        "<p>To turn on sign-in, set <b>TASKMAN_GOOGLE_CLIENT_ID</b>, <b>TASKMAN_GOOGLE_CLIENT_SECRET</b> (or "
        "<b>TASKMAN_GOOGLE_CLIENT_SECRET_FILE</b>), <b>TASKMAN_PUBLIC_URL</b>, and <b>TASKMAN_ALLOWED_EMAILS</b> or "
        "<b>TASKMAN_ALLOWED_DOMAINS</b>, then start the server again.</p>"
        "<p>On your own computer, use local mode instead: <b>TASKMAN_LOCAL_MODE=1</b> with "
        "<b>TASKMAN_LOCAL_PERSON</b>, as the Docker Compose stack does.</p>"
    ))


def error_page(message: str) -> str:
    # The CLI listens on 127.0.0.1 and gets no answer when the login fails on the server.
    return page("Taskman login failed", (
        f"<h1>Login failed</h1><p>{html.escape(message)}</p>"
        "<p>A <b>taskman auth login</b> command that waits in a terminal does not stop by itself. "
        "Press Ctrl+C in that terminal, then run the command again.</p>"
    ))
