"""Browser login with Google, personal access tokens on a web page, and the `taskman auth` commands.

A fake Google answers the server's code exchange, so no test reaches the internet.
"""
from __future__ import annotations

import base64
import json
import os
import stat
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from taskman.login import LoginSettings, Provider, challenge_for, login_settings
from taskman.storage.accounts import list_tokens
from taskman.storage.credentials import credentials_path, load_credentials
from taskman.storage.store import StorageConfigError
from tests.conftest import RunningServer
from tests.sql_helpers import new, run, show

CLIENT_ID = "client-123.apps.example"
PROVIDER = Provider(authorize_url="https://accounts.example/auth", token_url="https://accounts.example/token",
                    issuers=("https://accounts.example",))


def jwt(claims: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class FakeGoogle:
    """Issues sign-in codes and answers the token endpoint like Google."""

    def __init__(self) -> None:
        self.codes: dict[str, dict] = {}
        self.forms: list[dict] = []

    def sign_in(self, authorize_url: str, email: str = "ada@example.com", **changes) -> dict[str, str]:
        """What the browser holds after Google signs the person in: the server's callback query."""
        query = {key: values[0] for key, values in parse_qs(urlsplit(authorize_url).query).items()}
        claims = {"iss": "https://accounts.example", "aud": CLIENT_ID, "email": email, "email_verified": True,
                  "nonce": query["nonce"], "exp": int(datetime.now(timezone.utc).timestamp()) + 600}
        claims.update(changes)
        code = f"code-{len(self.codes)}"
        self.codes[code] = {"claims": claims, "challenge": query["code_challenge"]}
        return {"state": query["state"], "code": code}

    def post_form(self, url: str, form: dict[str, str]) -> dict:
        self.forms.append(form)
        assert url == PROVIDER.token_url
        issued = self.codes.pop(form["code"])
        assert challenge_for(form["code_verifier"]) == issued["challenge"]
        return {"id_token": jwt(issued["claims"]), "access_token": "unused"}


def settings(url: str = "http://127.0.0.1:1", **changes) -> LoginSettings:
    values = {"client_id": CLIENT_ID, "client_secret": "secret", "public_url": url,
              "emails": frozenset({"ada@example.com"}), "domains": frozenset({"team.example"}), "provider": PROVIDER}
    return LoginSettings(**(values | changes))


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def login_server(sqlite_env, google):
    running = RunningServer(sqlite_env, login=settings(), post_form=google.post_form)
    yield running
    running.stop()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get(url: str) -> tuple[int, dict, str]:
    """A browser request that does not follow redirects. Returns status, headers and body."""
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def post(url: str, data: dict) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def start_cli_login(server, verifier: str = "v" * 50, port: int = 50000, state: str = "s" * 20) -> str:
    """The provider address that ``/auth/cli`` sends the browser to."""
    query = f"port={port}&state={state}&challenge={challenge_for(verifier)}&label=laptop"
    status, headers, _ = get(f"{server.url}/auth/cli?{query}")
    assert status == 302, headers
    return headers["Location"]


def callback(server, query: dict) -> tuple[int, dict, str]:
    return get(f"{server.url}/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items()))


# ---- the server's login steps ---------------------------------------------


def test_cli_login_returns_a_token_for_the_verified_email(login_server, google):
    verifier = "v" * 50
    authorize = start_cli_login(login_server, verifier)
    query = parse_qs(urlsplit(authorize).query)
    assert authorize.startswith(PROVIDER.authorize_url)
    assert query["client_id"] == [CLIENT_ID] and query["scope"] == ["openid email"]
    assert query["redirect_uri"] == ["http://127.0.0.1:1/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]

    status, headers, _ = callback(login_server, google.sign_in(authorize))
    back = urlsplit(headers["Location"])
    assert status == 302 and back.netloc == "127.0.0.1:50000" and back.path == "/callback"
    returned = {key: values[0] for key, values in parse_qs(back.query).items()}
    assert returned["state"] == "s" * 20
    assert google.forms[0]["client_secret"] == "secret"

    status, answer = post(f"{login_server.url}/auth/exchange", {"code": returned["code"], "verifier": verifier})
    assert status == 200 and answer["person"] == "ada@example.com"
    assert [(row["person"], row["label"]) for row in list_tokens(login_server.store)][-1] == ("ada@example.com", "laptop")
    request = urllib.request.Request(f"{login_server.url}/api/v1/whoami")
    request.add_header("Authorization", f"Bearer {answer['token']}")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert json.loads(response.read()) == {"person": "ada@example.com"}

    status, again = post(f"{login_server.url}/auth/exchange", {"code": returned["code"], "verifier": verifier})
    assert status == 400 and "already used" in again["error"]["message"]


def test_a_member_of_an_allowed_workspace_signs_in(login_server, google):
    authorize = start_cli_login(login_server)
    status, _, _ = callback(login_server, google.sign_in(authorize, email="Bob@Team.example", hd="Team.example"))
    assert status == 302


def test_an_address_in_the_domain_without_workspace_membership_is_refused(login_server, google):
    authorize = start_cli_login(login_server)
    status, _, body = callback(login_server, google.sign_in(authorize, email="bob@team.example"))
    assert status == 403 and "may not use this Taskman server" in body
    authorize = start_cli_login(login_server)
    status, _, _ = callback(login_server, google.sign_in(authorize, email="bob@team.example", hd="other.example"))
    assert status == 403


def test_the_exchange_needs_the_verifier_of_the_cli_that_started(login_server, google):
    authorize = start_cli_login(login_server, verifier="v" * 50)
    _, headers, _ = callback(login_server, google.sign_in(authorize))
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    status, answer = post(f"{login_server.url}/auth/exchange", {"code": code, "verifier": "w" * 50})
    assert status == 400 and "another program" in answer["error"]["message"]
    assert all(row["person"] != "ada@example.com" for row in list_tokens(login_server.store))


def test_an_exchange_code_expires_after_two_minutes(login_server, google):
    authorize = start_cli_login(login_server)
    _, headers, _ = callback(login_server, google.sign_in(authorize))
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    old = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    login_server.store.conn.execute("UPDATE logins SET finished_at = ?", (old,))
    status, answer = post(f"{login_server.url}/auth/exchange", {"code": code, "verifier": "v" * 50})
    assert status == 400 and "expired" in answer["error"]["message"]


@pytest.mark.parametrize(
    ("changes", "status", "message"),
    [
        ({"email": "eve@elsewhere.example"}, 403, "may not use this Taskman server"),
        ({"email_verified": False}, 403, "did not confirm an email"),
        ({"aud": "other-client"}, 502, "not for this login"),
        ({"iss": "https://evil.example"}, 502, "not for this login"),
        ({"nonce": "replayed"}, 502, "not for this login"),
        ({"exp": 1}, 502, "not for this login"),
    ],
)
def test_a_wrong_identity_gets_no_token(login_server, google, changes, status, message):
    before = list_tokens(login_server.store)
    authorize = start_cli_login(login_server)
    got, headers, body = callback(login_server, google.sign_in(authorize, **changes))
    assert got == status and message in body and "Location" not in headers
    assert list_tokens(login_server.store) == before


def test_a_sign_in_state_works_one_time(login_server, google):
    authorize = start_cli_login(login_server)
    query = google.sign_in(authorize)
    assert callback(login_server, query)[0] == 302
    status, _, body = callback(login_server, query)
    assert status == 400 and "already used" in body
    assert callback(login_server, {"state": "unknown", "code": "x"})[0] == 400


def test_a_sign_in_older_than_ten_minutes_is_refused(login_server, google):
    authorize = start_cli_login(login_server)
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    login_server.store.conn.execute("UPDATE logins SET created_at = ?", (old,))
    status, _, body = callback(login_server, google.sign_in(authorize))
    assert status == 400 and "more than 10 minutes" in body


def test_a_cancelled_google_sign_in_is_reported(login_server):
    authorize = start_cli_login(login_server)
    state = parse_qs(urlsplit(authorize).query)["state"][0]
    status, _, body = callback(login_server, {"state": state, "error": "access_denied"})
    assert status == 400 and "access_denied" in body


class RefusingTokenEndpoint:
    """A local token endpoint that refuses each code exchange with one answer, as Google does.

    A body of ``None`` is a chunked body that the endpoint cuts off inside its first chunk.
    """

    def __init__(self, status: int, body: bytes | None) -> None:
        self.forms: list[dict] = []
        forms = self.forms

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                form = self.rfile.read(int(self.headers["Content-Length"])).decode()
                forms.append({key: values[0] for key, values in parse_qs(form).items()})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if body is None:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b'ff\r\n{"error"')
                    return
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/token"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.mark.parametrize(
    ("status", "body", "shown"),
    [
        (401, b'{"error": "invalid_client", "error_description": "The OAuth client was not found."}',
         "Google refused the sign-in (HTTP status 401, invalid_client)."),
        (400, b'{"error": "invalid_grant", "error_description": "Bad Request"}',
         "Google refused the sign-in (HTTP status 400, invalid_grant)."),
        (500, b"<html>Server Error</html>", "Google refused the sign-in (HTTP status 500)."),
        (401, b'{"error": "<script>alert(1)</script>"}', "Google refused the sign-in (HTTP status 401)."),
        (401, b'{"error": "invalid_client\\n"}', "Google refused the sign-in (HTTP status 401)."),
        (401, b'["invalid_client"]', "Google refused the sign-in (HTTP status 401)."),
        (401, None, "Google refused the sign-in (HTTP status 401)."),
    ],
)
def test_a_refused_code_exchange_shows_only_the_status_and_a_safe_error_code(sqlite_env, google, status, body, shown):
    secret = "GOCSPX-test-client-secret"
    endpoint = RefusingTokenEndpoint(status, body)
    provider = Provider(authorize_url=PROVIDER.authorize_url, token_url=endpoint.url, issuers=PROVIDER.issuers)
    # No post_form: the server sends the exchange to the endpoint with its own HTTP client.
    running = RunningServer(sqlite_env, login=settings(client_secret=secret, provider=provider))
    try:
        query = google.sign_in(start_cli_login(running))
        got, headers, page = callback(running, query)
    finally:
        running.stop()
        endpoint.stop()
    assert endpoint.forms[0]["code"] == query["code"] and endpoint.forms[0]["client_secret"] == secret
    assert got == 502 and shown in page and "Location" not in headers
    assert "Press Ctrl+C in that terminal, then run the command again." in page
    for hidden in (query["code"], query["state"], secret, "was not found", "Bad Request", "Server Error", "alert"):
        assert hidden not in page


def test_the_request_log_leaves_out_the_query_of_login_paths(sqlite_env, google, capsys):
    running = RunningServer(sqlite_env, login=settings(), post_form=google.post_form, log=True)
    try:
        query = google.sign_in(start_cli_login(running))
        assert callback(running, query)[0] == 302
        assert get(f"{running.url}/readyz?probe=1")[0] == 200
    finally:
        running.stop()
    log = capsys.readouterr().err
    assert '"GET /auth/cli HTTP/1.1" 302' in log and '"GET /auth/callback HTTP/1.1" 302' in log
    assert query["code"] not in log and query["state"] not in log and "challenge=" not in log
    assert '"GET /readyz?probe=1 HTTP/1.1" 200' in log


@pytest.mark.parametrize(
    "query",
    [
        "state=ssssssssssssssssssss&challenge=" + "c" * 43,
        "port=80&state=ssssssssssssssssssss&challenge=" + "c" * 43,
        "port=50000&state=short&challenge=" + "c" * 43,
        "port=50000&state=ssssssssssssssssssss&challenge=short",
        "port=50000&state=ssssssssssssssssssss&challenge=" + "c" * 43 + "&label=" + "x" * 61,
    ],
)
def test_a_wrong_login_address_is_refused(login_server, query):
    status, headers, _ = get(f"{login_server.url}/auth/cli?{query}")
    assert status == 400 and "Location" not in headers


def test_the_token_page_shows_a_personal_access_token_one_time(login_server, google):
    status, headers, _ = get(f"{login_server.url}/auth/token?label=build-vm")
    assert status == 302
    status, headers, body = callback(login_server, google.sign_in(headers["Location"]))
    assert status == 200 and "tmn_" in body and "ada@example.com" in body and "Ctrl+C" not in body
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert list_tokens(login_server.store)[-1]["label"] == "build-vm"


def test_login_pages_without_login_settings(server):
    status, _, body = get(f"{server.url}/auth/cli?port=50000")
    assert status == 404 and "not set up" in body
    assert post(f"{server.url}/auth/exchange", {"code": "x", "verifier": "y"})[0] == 404


def test_a_failure_inside_login_gives_a_page_without_details(login_server, monkeypatch):
    monkeypatch.setattr(login_server.logins, "start", lambda *args: 1 / 0)
    status, _, body = get(f"{login_server.url}/auth/cli?port=50000")
    assert status == 500 and "The Taskman server failed." in body and "ZeroDivision" not in body


# ---- settings -------------------------------------------------------------


def test_login_settings_from_the_environment(tmp_path):
    assert login_settings({}) is None
    secret = tmp_path / "secret"
    secret.write_text("from-file\n")
    loaded = login_settings({
        "TASKMAN_GOOGLE_CLIENT_ID": "id", "TASKMAN_GOOGLE_CLIENT_SECRET_FILE": str(secret),
        "TASKMAN_PUBLIC_URL": "https://taskman.example.com/", "TASKMAN_ALLOWED_EMAILS": "A@x.example, b@x.example",
    })
    assert loaded.client_secret == "from-file" and loaded.public_url == "https://taskman.example.com"
    assert loaded.emails == {"a@x.example", "b@x.example"} and loaded.provider == Provider()


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("TASKMAN_GOOGLE_CLIENT_SECRET", "TASKMAN_GOOGLE_CLIENT_SECRET"),
        ("TASKMAN_PUBLIC_URL", "TASKMAN_PUBLIC_URL"),
        ("TASKMAN_ALLOWED_DOMAINS", "TASKMAN_ALLOWED_EMAILS or TASKMAN_ALLOWED_DOMAINS"),
    ],
)
def test_partial_login_settings_are_refused(missing, message):
    environ = {"TASKMAN_GOOGLE_CLIENT_ID": "id", "TASKMAN_GOOGLE_CLIENT_SECRET": "s",
               "TASKMAN_PUBLIC_URL": "https://taskman.example.com", "TASKMAN_ALLOWED_DOMAINS": "x.example"}
    del environ[missing]
    with pytest.raises(StorageConfigError, match=message):
        login_settings(environ)


def test_serve_refuses_partial_login_settings(sqlite_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_GOOGLE_CLIENT_ID", "id")
    assert "TASKMAN_GOOGLE_CLIENT_SECRET" in run("serve", code=2)


# ---- taskman auth ---------------------------------------------------------


def _browser(server, google, email="ada@example.com"):
    """Stands in for webbrowser.open: walks the redirects like a browser, in the background."""

    def follow(start_url: str) -> bool:
        def walk() -> None:
            _, headers, _ = get(start_url)
            _, headers, _ = callback(server, google.sign_in(headers["Location"], email=email))
            get(headers["Location"])

        threading.Thread(target=walk, daemon=True).start()
        return True

    return follow


def test_auth_login_through_the_browser_then_commands_use_the_server(login_server, google, monkeypatch):
    monkeypatch.setattr("webbrowser.open", _browser(login_server, google))
    output = run("auth", "login", "--server", login_server.url, "--label", "laptop")
    assert f"Logged in to {login_server.url} as ada@example.com." in output
    path = credentials_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_credentials()["server"] == login_server.url
    assert "tmn_" not in output

    key = new("After login")
    assert show(key)["frontmatter"]["created_by"] == "ada@example.com"
    assert f"as ada@example.com. Token from {path}" in run("auth", "status")


def test_auth_login_with_a_token_from_standard_input(server):
    output = run("auth", "login", "--server", server.url, "--with-token", input=server.token + "\n")
    assert f"as Vlad." in output and server.token not in output
    assert new("With token") == "TM-1"


def test_auth_login_refuses_a_wrong_token_and_saves_nothing(server):
    assert "refused the token" in run("auth", "login", "--server", server.url, "--with-token", input="tmn_bad\n", code=2)
    assert not credentials_path().exists()
    assert "Paste the token" in run("auth", "login", "--server", server.url, "--with-token", input="\n", code=2)


def test_auth_login_needs_a_safe_server_address():
    assert "Name the server" in run("auth", "login", code=2)
    assert "must use https://" in run("auth", "login", "--server", "http://taskman.example.com", code=2)


def test_auth_login_stops_when_the_browser_never_returns(login_server, monkeypatch):
    from taskman.commands import auth

    monkeypatch.setattr("webbrowser.open", lambda url: True)
    monkeypatch.setattr(auth, "LOGIN_WAIT_SECONDS", 0.2)
    assert "No login within" in run("auth", "login", "--server", login_server.url, code=1)
    assert not credentials_path().exists()


def test_auth_logout_revokes_the_token_on_the_server(server):
    run("auth", "login", "--server", server.url, "--with-token", input=server.token + "\n")
    assert f"Logged out of {server.url}." in run("auth", "logout")
    assert list_tokens(server.store)[0]["revoked_at"]
    assert server.url not in json.dumps(load_credentials())
    assert "Not logged in." in run("auth", "logout")
    assert "No Taskman server is set." in run("auth", "status", code=1)


def test_auth_status_without_a_server_names_the_local_database(sqlite_env, tmp_path, monkeypatch):
    path = sqlite_env.sqlite_path
    assert run("auth", "status", code=1) == f"No Taskman server is set. Taskman uses the SQLite database at {path}.\n"
    missing = tmp_path / "none.db"
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{missing}")
    assert run("auth", "status", code=1) == (
        f"No Taskman server is set. Taskman uses the SQLite database at {missing}. That file does not exist.\n")
    url = "postgresql://taskman:secretpw@db.example.com/taskman"
    monkeypatch.setenv("TASKMAN_BACKEND", "postgres")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", url)
    output = run("auth", "status", code=1)
    assert output == "No Taskman server is set. Taskman uses the Postgres database of the storage settings.\n"
    monkeypatch.setenv("TASKMAN_BACKEND", "nosuch")
    assert run("auth", "status", code=1).startswith("Error: Unknown storage backend 'nosuch'")


def test_the_environment_token_wins_over_the_saved_login(server, monkeypatch):
    run("auth", "login", "--server", server.url, "--with-token", input=server.token + "\n")
    _, other = __import__("taskman.storage.accounts", fromlist=["create_token"]).create_token(server.store, "Ada")
    monkeypatch.setenv("TASKMAN_TOKEN", other)
    assert "as Ada. Token from TASKMAN_TOKEN." in run("auth", "status")
    assert "TASKMAN_TOKEN is still set" in run("auth", "logout")


def test_a_damaged_login_file_is_reported(server):
    path = credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert "Delete it and run: taskman auth login" in run("list", code=2)


def test_the_auth_commands_report_a_damaged_login_file_in_one_line(server):
    path = credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    message = f"Error: Taskman cannot read {path}. Delete it and run: taskman auth login\n"
    assert run("auth", "status", code=1) == message
    assert run("auth", "logout", code=2) == message
    assert run("auth", "login", "--with-token", input=server.token + "\n", code=2) == message
    # The server accepts the token; the CLI cannot save it.
    assert run("auth", "login", "--server", server.url, "--with-token", input=server.token + "\n", code=2) == message
    path.unlink()
    assert "Logged in to" in run("auth", "login", "--server", server.url, "--with-token", input=server.token + "\n")


def test_the_auth_commands_report_a_settings_error_in_one_line(server, monkeypatch):
    monkeypatch.setenv("TASKMAN_SERVER_URL", "http://taskman.example.com")
    message = "Error: The Taskman server address must use https:// unless the server runs on this machine.\n"
    assert run("auth", "status", code=1) == message
    assert run("auth", "login", "--with-token", input=server.token + "\n", code=2) == message
