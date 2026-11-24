"""The workspace page on a hosted server: Google sign-in, the session cookie, CSRF and Origin checks,
the author of each change, sign-out, the page policy and the settings that exclude each other.

A fake Google answers the server's code exchange, as in ``tests/test_login.py``.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import http.client
import json
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

import taskman.login
from taskman import workspace
from taskman.cli import app
from taskman.login import LOGIN_COOKIE, SESSION_COOKIE, _return_path, csrf_token
from taskman.storage.accounts import list_tokens
from tests.conftest import RunningServer
from tests.sql_helpers import new, run, show
from tests.test_login import PROVIDER, FakeGoogle, NoRedirect, settings

runner = CliRunner()
AUTH_POLICY = "default-src 'none'; style-src 'unsafe-inline'"


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def hosted(sqlite_env, google):
    """A hosted server: Google sign-in for ada@example.com and bob@example.com, at its own address."""
    running = RunningServer(sqlite_env, login=settings(emails=frozenset({"ada@example.com", "bob@example.com"})),
                            post_form=google.post_form)
    running.logins.settings = dataclasses.replace(running.logins.settings, public_url=running.url)
    yield running
    running.stop()


def call(server, path: str, data: dict | None = None, headers: dict | None = None,
         method: str | None = None) -> tuple[int, dict, str]:
    """A request that does not follow redirects. Returns status, headers and body. POST when ``data`` is given."""
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(server.url + path, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def login_cookie(headers: dict) -> dict:
    """The browser's sign-in cookie that a redirect to the provider sets, as the browser sends it back."""
    match = re.match(rf"({re.escape(LOGIN_COOKIE)}=[A-Za-z0-9_-]+);", headers["Set-Cookie"])
    return {"Cookie": match.group(1)}


def sign_in(server, google, path: str = "/", email: str = "ada@example.com") -> tuple[str, dict, str]:
    """Open ``path`` without a session and sign in. Returns the session secret and the callback's headers."""
    status, headers, _ = call(server, path)
    assert status == 302 and headers["Location"].startswith(PROVIDER.authorize_url), (status, headers)
    query = google.sign_in(headers["Location"], email=email)
    status, headers, body = call(server, "/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items()),
                                 headers=login_cookie(headers))
    assert status == 302, body
    match = re.match(rf"{re.escape(SESSION_COOKIE)}=([A-Za-z0-9_-]+);", headers["Set-Cookie"])
    return match.group(1), headers, body


def page_headers(server, secret: str, csrf: str | None = None, origin: str | None = None) -> dict:
    """The headers of the page's change, as the browser sends them."""
    headers = {"Content-Type": "application/json", "X-Taskman": "1", "Cookie": f"{SESSION_COOKIE}={secret}",
               "Origin": server.url if origin is None else origin}
    headers["X-Taskman-CSRF"] = csrf_token(secret) if csrf is None else csrf
    return headers


def cookie_of(secret: str) -> dict:
    return {"Cookie": f"{SESSION_COOKIE}={secret}"}


def state(server, key: str) -> tuple:
    """Everything a refused change could alter, the tokens of Settings > Tokens included."""
    item = server.store.get(key)
    return (item.metadata, item.body, server.store.activity(key), [i.id for i in server.store.items()],
            server.store.sibling_order(), list_tokens(server.store))


# ---- sign-in -------------------------------------------------------------


def test_a_signed_out_browser_signs_in_and_returns_to_the_item(hosted, google):
    key = new("Shown item")
    path = f"/items/{key}?view=done&q=a%20b"
    secret, headers, _ = sign_in(hosted, google, path)
    assert headers["Location"] == path
    cookie = headers["Set-Cookie"]
    assert cookie == f"{SESSION_COOKIE}={secret}; Max-Age=604800; Path=/; Secure; HttpOnly; SameSite=Lax"

    status, headers, text = call(hosted, path, headers=cookie_of(secret))
    assert (status, headers["Content-Type"]) == (200, "text/html; charset=utf-8")
    assert 'id="preview"' in text and f'<meta name="taskman-csrf" content="{csrf_token(secret)}" />' in text
    status, _, text = call(hosted, f"/api/show/{key}", headers=cookie_of(secret))
    shown = json.loads(text)
    assert status == 200 and shown["frontmatter"]["title"] == "Shown item"
    # Copy link gives the public address of the server, whatever Host the request names.
    assert shown["link"] == f"{hosted.url}/items/{key}"
    status, _, text = call(hosted, "/api/query", {}, page_headers(hosted, secret))
    assert status == 200 and json.loads(text)["actor"] == "ada@example.com"


def test_the_database_keeps_only_a_fingerprint_of_the_session_secret(hosted, google):
    secret, _, _ = sign_in(hosted, google)
    rows = hosted.store.conn.execute(
        "SELECT secret_hash, person, created_at, expires_at, ended_at FROM sessions").fetchall()
    assert len(rows) == 1
    secret_hash, person, created_at, expires_at, ended_at = rows[0]
    assert secret_hash == hashlib.sha256(secret.encode()).hexdigest() and person == "ada@example.com"
    assert datetime.fromisoformat(expires_at) - datetime.fromisoformat(created_at) == timedelta(days=7)
    assert ended_at is None
    assert all(secret not in str(value) for value in rows[0])
    assert all(secret not in str(value) for row in hosted.store.conn.execute("SELECT * FROM logins") for value in row)


def test_another_server_instance_accepts_the_session(hosted, google, sqlite_env):
    secret, _, _ = sign_in(hosted, google)
    other = RunningServer(sqlite_env, login=hosted.logins.settings, post_form=google.post_form)
    try:
        other.logins.settings = dataclasses.replace(other.logins.settings, public_url=other.url)
        status, _, text = call(other, "/", headers=cookie_of(secret))
        assert status == 200 and 'id="preview"' in text
    finally:
        other.stop()


@pytest.mark.parametrize("changes", [{"email": "eve@elsewhere.example"}, {"email_verified": False}])
def test_a_person_who_may_not_sign_in_gets_a_refusal_page_and_no_cookie(hosted, google, changes):
    status, headers, _ = call(hosted, "/")
    query = google.sign_in(headers["Location"], **changes)
    status, headers, body = call(hosted, "/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items()),
                                 headers=login_cookie(headers))
    assert status == 403 and "Set-Cookie" not in headers and "Location" not in headers
    assert "Sign-in failed" in body and "Open Taskman again" in body and "taskman auth login" not in body
    assert ("may not use this Taskman server" if "email" in changes else "did not confirm an email") in body
    assert headers["Content-Security-Policy"] == AUTH_POLICY
    assert hosted.store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_an_unknown_or_used_sign_in_state_gives_no_cookie(hosted, google):
    status, headers, _ = call(hosted, "/")
    query = google.sign_in(headers["Location"])
    callback = "/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items())
    browser = login_cookie(headers)
    assert call(hosted, callback, headers=browser)[0] == 302
    # A reload of the callback address: the page of the workspace sign-in, not the one of `taskman auth login`.
    status, headers, body = call(hosted, callback, headers=browser)
    assert status == 400 and "already used" in body and "Set-Cookie" not in headers
    assert "Sign-in failed" in body and "Open Taskman again" in body and "taskman auth login" not in body


def test_a_sign_in_finished_in_another_browser_gives_no_session(hosted, google):
    """A person who may sign in sends the callback address of their own sign-in to another person (login CSRF)."""
    status, headers, _ = call(hosted, "/")
    started = login_cookie(headers)
    assert re.fullmatch(rf"{re.escape(LOGIN_COOKIE)}=[A-Za-z0-9_-]+; Max-Age=600; Path=/; Secure; HttpOnly; "
                        "SameSite=Lax", headers["Set-Cookie"])
    _, other_headers, _ = call(hosted, "/")
    query = google.sign_in(headers["Location"], email="bob@example.com")
    callback = "/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items())
    for browser in ({}, login_cookie(other_headers), {"Cookie": f"{LOGIN_COOKIE}=" + "x" * 43}):
        status, response_headers, body = call(hosted, callback, headers=browser)
        assert status == 400 and "Set-Cookie" not in response_headers, (browser, body)
        assert "Sign-in failed" in body and ("another browser" in body or "already used" in body)
    assert hosted.store.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    # The refused callback used the sign-in: the browser that started it cannot finish it either.
    assert call(hosted, callback, headers=started)[0] == 400
    assert google.forms == []


def test_two_sign_ins_of_one_browser_both_finish(hosted, google):
    """Two tabs of one browser that reload through sign-in at the same time each get a session."""
    status, first, _ = call(hosted, "/")
    browser = login_cookie(first)
    status, second, _ = call(hosted, "/items/TM-1", headers=browser)
    assert login_cookie(second) == browser
    for headers, path in ((first, "/"), (second, "/items/TM-1")):
        query = google.sign_in(headers["Location"])
        status, response_headers, body = call(hosted, "/auth/callback?" + "&".join(
            f"{k}={v}" for k, v in query.items()), headers=browser)
        assert (status, response_headers["Location"]) == (302, path), body


@pytest.mark.parametrize("value", [
    "//evil.example/x", "/\\evil.example", "/\\/evil.example", "https://evil.example/", "evil.example", "",
    "/ok\\..\\x", "/items/TM-1 x", "/" + "a" * 2001, "/\x00", "/x\n", "/items/TM-1\r\n",
])
def test_a_return_address_off_this_server_returns_to_the_root(value):
    assert _return_path(value) == "/"


@pytest.mark.parametrize("value", ["/", "/items/TM-1", "/?view=done&q=a%20b", "/items/TM-1?panel=1#x", "/?next=//x"])
def test_a_return_address_on_this_server_is_kept(value):
    assert _return_path(value) == value


def test_a_return_address_with_a_line_break_does_not_end_the_callback_headers(hosted, google):
    """A line break at the end of the remembered address would end the headers before the session cookie."""
    status, headers, _ = call(hosted, "/auth/ui?path=%2Fitems%2FTM-1%0A")
    assert status == 302
    query = google.sign_in(headers["Location"])
    status, headers, body = call(hosted, "/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items()),
                                 headers=login_cookie(headers))
    assert (status, headers["Location"]) == (302, "/"), body
    assert headers["Set-Cookie"].startswith(f"{SESSION_COOKIE}=")


def test_a_request_line_with_another_host_never_redirects_there(hosted):
    """``GET //evil.example/`` must not lead the browser to the other host, before or after sign-in."""
    connection = http.client.HTTPConnection(urlsplit(hosted.url).netloc, timeout=10)
    connection.putrequest("GET", "//evil.example/", skip_host=True)
    connection.putheader("Host", urlsplit(hosted.url).netloc)
    connection.endheaders()
    response = connection.getresponse()
    location = response.getheader("Location") or ""
    connection.close()
    assert response.status == 404 and "evil" not in location
    with hosted.store.lock():
        paths = {row[0] for row in hosted.store.conn.execute("SELECT return_path FROM logins")}
    assert all(path is None or not path.startswith("//") for path in paths)


# ---- the author of each change ---------------------------------------------


def test_each_signed_in_person_is_the_author_of_their_changes(hosted, google):
    key = new("Shared item")
    ada, _, _ = sign_in(hosted, google, email="ada@example.com")
    bob, _, _ = sign_in(hosted, google, email="bob@example.com")

    def comment(secret: str, text: str) -> None:
        status, _, body = call(hosted, "/api/comment", {"id": key, "kind": "progress", "text": text},
                               page_headers(hosted, secret))
        assert status == 200, body

    # The two persons work at the same time: the server's threads share one store.
    threads = [threading.Thread(target=comment, args=(secret, f"{name} {n}"))
               for n in range(10) for name, secret in (("ada@example.com", ada), ("bob@example.com", bob))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    journal = show(key)["frontmatter"]["journal"]
    assert len(journal) == 20
    assert all(entry["actor"] == entry["text"].split()[0] for entry in journal)

    assert call(hosted, "/api/assign", {"id": key, "assignee": "Codex"}, page_headers(hosted, bob))[0] == 200
    assert call(hosted, "/api/label", {"id": key, "add": ["ui"]}, page_headers(hosted, ada))[0] == 200
    lines = run("activity", key).splitlines()
    assert "  bob@example.com  assignee: - -> Codex" in lines[-2]
    assert "  ada@example.com  labels:" in lines[-1]
    # The same change through the API with a token has the token's person as author.
    request = urllib.request.Request(hosted.url + "/api/v1/operations/assign", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {hosted.token}"},
        data=json.dumps({"arguments": {"item_id": key, "assignee": "Ada"}}).encode())
    urllib.request.urlopen(request, timeout=10).close()
    assert hosted.store.activity(key)[-1]["author"] == "Vlad"


# ---- CSRF and Origin checks --------------------------------------------------

_CHANGE = {"id": "", "title": "Changed", "body": "Changed", "project": "TM", "type": "task", "kind": "progress",
           "text": "Cross-site", "assignee": "Mallory", "add": ["x"], "remove": [], "labels": ["x"],
           "status": "active", "repositories": ["github.com/evil/repo"], "position": 0}


@pytest.mark.parametrize("path", workspace.POSTS + ("/auth/logout",))
def test_a_change_without_the_csrf_key_or_from_another_origin_is_refused(hosted, google, path):
    key = new("Kept")
    new("Sibling")
    ada, _, _ = sign_in(hosted, google)
    bob, _, _ = sign_in(hosted, google, email="bob@example.com")
    data = _CHANGE | {"id": key}
    before = state(hosted, key)
    without_key = page_headers(hosted, ada)
    del without_key["X-Taskman-CSRF"]
    without_origin = page_headers(hosted, ada)
    del without_origin["Origin"]
    for headers, kind in (
        (without_key, "csrf"),
        (page_headers(hosted, ada, csrf=""), "csrf"),
        # A wrong key from a page of this server: the page holds the key of an earlier session and reloads.
        (page_headers(hosted, ada, csrf="0" * 64), "stale"),
        (page_headers(hosted, ada, csrf=csrf_token(bob)), "stale"),  # the key of another session
        (page_headers(hosted, ada, csrf="\u00e9" * 64), "stale"),  # a key that is not ASCII
        (page_headers(hosted, ada, csrf="0" * 64, origin="https://evil.example"), "csrf"),
        (page_headers(hosted, ada, origin="https://evil.example"), "csrf"),
        (page_headers(hosted, ada, origin="null"), "csrf"),
        (page_headers(hosted, ada, origin=hosted.url.replace("127.0.0.1", "localhost")), "csrf"),
        (without_origin, "csrf"),
    ):
        status, _, body = call(hosted, path, data, headers)
        assert status == 403 and json.loads(body)["error"]["kind"] == kind, (headers, body)
    assert state(hosted, key) == before
    # The session still works: no refused request ended it.
    assert call(hosted, "/", headers=cookie_of(ada))[0] == 200


def test_the_same_change_with_the_csrf_key_and_the_origin_is_accepted(hosted, google):
    key = new("Changed")
    ada, _, _ = sign_in(hosted, google)
    status, _, body = call(hosted, "/api/comment", _CHANGE | {"id": key}, page_headers(hosted, ada))
    assert status == 200, body
    assert show(key)["frontmatter"]["journal"][-1]["actor"] == "ada@example.com"


@pytest.mark.parametrize("public_url, origin", [
    ("http://LocalHost:{port}", "http://localhost:{port}"),
    ("https://Taskman.Example.com", "https://taskman.example.com"),
    ("https://taskman.example.com:443", "https://taskman.example.com"),
    ("http://taskman.example.com:80/", "http://taskman.example.com"),
    ("HTTPS://taskman.example.com:8443", "https://taskman.example.com:8443"),
])
def test_the_origin_check_accepts_the_origin_that_a_browser_sends(hosted, google, public_url, origin):
    """A browser sends the host in lower case and leaves out the default port of the scheme."""
    port = urlsplit(hosted.url).port
    key = new("Changed")
    hosted.logins.settings = dataclasses.replace(hosted.logins.settings,
                                                 public_url=public_url.format(port=port).rstrip("/"))
    ada, _, _ = sign_in(hosted, google)
    status, _, body = call(hosted, "/api/comment", _CHANGE | {"id": key}, page_headers(hosted, ada,
                                                                                       origin=origin.format(port=port)))
    assert status == 200, body
    for other in ("https://taskman.example.com:8444", "http://taskman.example.com:443", "https://evil.example"):
        status, _, body = call(hosted, "/api/comment", _CHANGE | {"id": key}, page_headers(hosted, ada, origin=other))
        assert status == 403 and json.loads(body)["error"]["kind"] == "csrf"


@pytest.mark.parametrize("path", workspace.POSTS)
def test_a_change_without_a_session_gets_401(hosted, path):
    key = new("Kept")
    before = state(hosted, key)
    headers = {"Content-Type": "application/json", "X-Taskman": "1", "Origin": hosted.url}
    for extra in ({}, cookie_of("x" * 43), {"Cookie": "other=1"}):
        status, response_headers, body = call(hosted, path, _CHANGE | {"id": key}, headers | extra)
        assert status == 401 and json.loads(body)["error"]["kind"] == "session"
        assert "Set-Cookie" not in response_headers
    assert state(hosted, key) == before


# ---- sign-out and expiry ------------------------------------------------------


def _signed_out(server, secret: str, key: str) -> None:
    """The cookie no longer signs in: the page goes to sign-in, and data calls get 401."""
    for path in ("/", f"/items/{key}"):
        status, headers, _ = call(server, path, headers=cookie_of(secret))
        assert status == 302 and headers["Location"].startswith(PROVIDER.authorize_url), path
    assert call(server, f"/api/show/{key}", headers=cookie_of(secret))[0] == 401
    before = state(server, key)
    for path in ("/api/query", "/api/comment"):
        status, _, body = call(server, path, {"id": key, "kind": "progress", "text": "Late"},
                               page_headers(server, secret))
        assert status == 401 and json.loads(body)["error"]["message"] == "Your Taskman session ended. Sign in again."
    assert state(server, key) == before


def test_sign_out_ends_the_session_on_the_server(hosted, google):
    key = new("Item")
    ada, _, _ = sign_in(hosted, google)
    bob, _, _ = sign_in(hosted, google, email="bob@example.com")
    status, headers, body = call(hosted, "/auth/logout", {}, page_headers(hosted, ada))
    assert (status, json.loads(body)) == (200, {})
    assert headers["Set-Cookie"] == f"{SESSION_COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
    _signed_out(hosted, ada, key)
    assert call(hosted, "/auth/logout", {}, page_headers(hosted, ada))[0] == 401
    # The session of another person stays.
    assert call(hosted, "/", headers=cookie_of(bob))[0] == 200
    status, headers, body = call(hosted, "/auth/signed-out")
    assert status == 200 and "You signed out" in body and headers["Content-Security-Policy"] == AUTH_POLICY


def test_a_session_ends_seven_days_after_sign_in_without_renewal(hosted, google, monkeypatch):
    key = new("Item")
    start = datetime.now(timezone.utc)
    monkeypatch.setattr(taskman.login, "_now", lambda: start)
    ada, _, _ = sign_in(hosted, google)
    # Use on day 6 does not renew the session.
    monkeypatch.setattr(taskman.login, "_now", lambda: start + timedelta(days=6))
    assert call(hosted, "/", headers=cookie_of(ada))[0] == 200
    assert call(hosted, "/api/query", {}, page_headers(hosted, ada))[0] == 200
    monkeypatch.setattr(taskman.login, "_now", lambda: start + timedelta(days=7, seconds=-1))
    assert call(hosted, f"/api/show/{key}", headers=cookie_of(ada))[0] == 200
    monkeypatch.setattr(taskman.login, "_now", lambda: start + timedelta(days=7))
    _signed_out(hosted, ada, key)


def test_a_new_sign_in_removes_ended_and_expired_sessions(hosted, google):
    ended, _, _ = sign_in(hosted, google)
    expired, _, _ = sign_in(hosted, google, email="bob@example.com")
    kept, _, _ = sign_in(hosted, google)
    assert call(hosted, "/auth/logout", {}, page_headers(hosted, ended))[0] == 200
    hosted.store.conn.execute("UPDATE sessions SET expires_at = ? WHERE person = 'bob@example.com'",
                              ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    sign_in(hosted, google)
    hashes = {row[0] for row in hosted.store.conn.execute("SELECT secret_hash FROM sessions")}
    fingerprint = lambda secret: hashlib.sha256(secret.encode()).hexdigest()  # noqa: E731
    assert fingerprint(kept) in hashes and len(hashes) == 2
    assert fingerprint(ended) not in hashes and fingerprint(expired) not in hashes


# ---- headers --------------------------------------------------------------------


def test_the_workspace_page_has_its_own_content_security_policy(hosted, google):
    secret, _, _ = sign_in(hosted, google)
    status, headers, text = call(hosted, "/", headers=cookie_of(secret))
    assert status == 200 and headers["Cache-Control"] == "no-store"
    policy = dict(part.strip().split(" ", 1) for part in headers["Content-Security-Policy"].split(";"))
    scripts = re.findall(r"<script>(.*?)</script>", text, re.DOTALL)
    styles = re.findall(r"<style>(.*?)</style>", text, re.DOTALL)
    assert len(scripts) == 2 and len(styles) == 1

    def digest(code: str) -> str:
        return f"'sha256-{base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()}'"

    assert policy == {
        "default-src": "'none'", "script-src": " ".join(digest(code) for code in scripts),
        "style-src": digest(styles[0]), "connect-src": "'self'", "base-uri": "'none'", "form-action": "'self'",
        "frame-ancestors": "'none'",
    }
    assert "unsafe" not in headers["Content-Security-Policy"]


def test_the_auth_pages_keep_their_strict_policy(hosted, google):
    status, headers, _ = call(hosted, "/")
    assert status == 302 and headers["Content-Security-Policy"] == AUTH_POLICY
    for path in ("/auth/signed-out", "/auth/token", "/auth/ui", "/auth/callback?state=unknown"):
        assert call(hosted, path)[1]["Content-Security-Policy"] == AUTH_POLICY, path


def test_local_mode_keeps_its_page_without_a_script_policy_or_key(sqlite_env):
    running = RunningServer(sqlite_env, local_person="Ada")
    try:
        status, headers, text = call(running, "/")
        # Only frame protection: the local page has no script policy and no CSRF key.
        assert status == 200 and headers["Content-Security-Policy"] == "frame-ancestors 'none'"
        assert '<meta name="taskman-csrf"' not in text
    finally:
        running.stop()


def test_hosted_mode_keeps_the_api_and_the_cli_login(hosted, google):
    assert call(hosted, "/api/v1/items")[0] == 401
    status, _, body = call(hosted, "/api/v1/whoami", headers={"Authorization": f"Bearer {hosted.token}"})
    assert (status, json.loads(body)) == (200, {"person": "Vlad"})
    status, headers, _ = call(hosted, "/auth/cli?port=50000&state=" + "s" * 20 + "&challenge=" + "c" * 43)
    assert status == 302 and "Set-Cookie" not in headers


def test_the_request_log_holds_no_session_secret_or_csrf_key(sqlite_env, google, capsys):
    running = RunningServer(sqlite_env, login=settings(), post_form=google.post_form, log=True)
    try:
        running.logins.settings = dataclasses.replace(running.logins.settings, public_url=running.url)
        secret, _, _ = sign_in(running, google)
        assert call(running, "/api/query", {}, page_headers(running, secret))[0] == 200
        assert call(running, "/auth/logout", {}, page_headers(running, secret))[0] == 200
    finally:
        running.stop()
    log = capsys.readouterr().err
    assert '"GET / HTTP/1.1" 302' in log and '"POST /api/query HTTP/1.1" 200' in log
    assert secret not in log and csrf_token(secret) not in log and "code-0" not in log


# ---- settings -----------------------------------------------------------------


@pytest.mark.parametrize("complete, person", [(True, "Ada"), (False, "Ada"), (False, None)])
def test_local_mode_and_google_sign_in_do_not_start_together(sqlite_env, monkeypatch, complete, person):
    monkeypatch.setenv("TASKMAN_LOCAL_MODE", "1")
    if person:
        monkeypatch.setenv("TASKMAN_LOCAL_PERSON", person)
    else:
        # The person learns first that the two settings exclude each other, not that local mode needs a name.
        monkeypatch.delenv("TASKMAN_LOCAL_PERSON", raising=False)
    monkeypatch.setenv("TASKMAN_GOOGLE_CLIENT_ID", "id")
    if complete:
        monkeypatch.setenv("TASKMAN_GOOGLE_CLIENT_SECRET", "s")
        monkeypatch.setenv("TASKMAN_PUBLIC_URL", "https://taskman.example.com")
        monkeypatch.setenv("TASKMAN_ALLOWED_DOMAINS", "example.com")
    result = runner.invoke(app, ["serve", "--port", "0"])
    assert result.exit_code == 2, result.output
    assert ("Error: local mode (TASKMAN_LOCAL_MODE) and Google sign-in (TASKMAN_GOOGLE_CLIENT_ID) exclude each other. "
            "For a hosted server, unset TASKMAN_LOCAL_MODE. For the local Compose stack, unset the Google sign-in "
            "settings") in result.output
    assert "listens" not in result.output and "TASKMAN_LOCAL_PERSON" not in result.output
