"""Settings > Tokens (TASKMAN-53): a person lists, creates and revokes their own tokens on the workspace page.

The hosted server signs people in with a fake Google, as in ``tests/test_server_session.py``. Local mode acts for
the person of ``TASKMAN_LOCAL_PERSON``, as in ``tests/test_server_workspace.py``.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from taskman.storage.accounts import create_token, list_tokens
from tests.conftest import RunningServer
from tests.sql_helpers import run
from tests.test_login import FakeGoogle, settings, start_cli_login
from tests.test_server_session import call, cookie_of, page_headers, sign_in

LOCAL = {"Content-Type": "application/json", "X-Taskman": "1"}


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


@pytest.fixture
def local(sqlite_env):
    """A server in local mode for the person Ada."""
    running = RunningServer(sqlite_env, local_person="Ada")
    yield running
    running.stop()


def tokens(server, secret: str) -> list[dict]:
    status, _, body = call(server, "/api/tokens", {}, page_headers(server, secret))
    assert status == 200, body
    return json.loads(body)["tokens"]


def create(server, headers: dict, name="ci-agent", days=30) -> tuple[int, dict, dict]:
    status, response_headers, body = call(server, "/api/tokens/create", {"name": name, "days": days}, headers)
    return status, response_headers, json.loads(body)


def whoami(server, token: str) -> int:
    return call(server, "/api/v1/whoami", headers={"Authorization": f"Bearer {token}"})[0]


# ---- list, create and revoke ------------------------------------------------------


def test_the_list_shows_the_active_and_expired_tokens_of_the_person_newest_first(hosted, google):
    ada, _, _ = sign_in(hosted, google)
    old_id, old = create_token(hosted.store, "ada@example.com", "old-vm", 90)
    expired_id, _ = create_token(hosted.store, "ada@example.com", "expired-vm", 90)
    revoked_id, _ = create_token(hosted.store, "ada@example.com", "revoked-vm", 90)
    with hosted.store.lock():
        hosted.store.conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?",
                                  ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), expired_id))
    assert call(hosted, f"/api/v1/tokens/{revoked_id}/revoke", {}, {"Authorization": f"Bearer {old}"})[0] == 200
    assert whoami(hosted, old) == 200
    status, headers, body = call(hosted, "/api/tokens", {}, page_headers(hosted, ada))
    assert status == 200 and headers["Cache-Control"] == "no-store"
    answer = json.loads(body)
    assert answer["person"] == "ada@example.com"
    # Revoked tokens leave the list; the database keeps their rows.
    assert [(row["id"], row["name"], row["status"]) for row in answer["tokens"]] == [
        (expired_id, "expired-vm", "expired"), (old_id, "old-vm", "active")]
    assert [row["id"] for row in list_tokens(hosted.store, "ada@example.com")] == [old_id, expired_id, revoked_id]
    row = answer["tokens"][1]
    assert set(row) == {"id", "name", "created_at", "expires_at", "last_used_at", "status"}
    assert row["last_used_at"] and answer["tokens"][0]["last_used_at"] is None
    assert old not in body and "tmn_" not in body and "token_hash" not in body


def test_a_new_token_has_its_name_and_expiry_and_works_for_the_person(hosted, google):
    ada, _, _ = sign_in(hosted, google)
    status, headers, created = create(hosted, page_headers(hosted, ada), name="  ci-agent  ", days=30)
    assert status == 201 and created["name"] == "ci-agent" and created["token"].startswith("tmn_")
    assert call(hosted, "/api/v1/whoami", headers={"Authorization": f"Bearer {created['token']}"})[2] == \
        '{"person":"ada@example.com"}'
    (row,) = tokens(hosted, ada)
    assert (row["id"], row["name"], row["status"]) == (created["id"], "ci-agent", "active")
    lifetime = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
    assert lifetime == timedelta(days=30)
    for days in (7, 90, 365):
        assert create(hosted, page_headers(hosted, ada), name="x" * 60, days=days)[0] == 201
    assert len(tokens(hosted, ada)) == 4


@pytest.mark.parametrize("data, message", [
    ({"name": "", "days": 30}, "Enter a name of 1 to 60 characters"),
    ({"name": "   ", "days": 30}, "Enter a name of 1 to 60 characters"),
    ({"name": "x" * 61, "days": 30}, "Enter a name of 1 to 60 characters"),
    ({"days": 30}, "Enter a name of 1 to 60 characters"),
    ({"name": 7, "days": 30}, "Enter a name of 1 to 60 characters"),
    # Postgres text cannot hold NUL; an invisible or reordering character makes the name look empty or false.
    ({"name": "a\u0000b", "days": 30}, "A name cannot hold control or invisible characters"),
    ({"name": "\u200b", "days": 30}, "A name cannot hold control or invisible characters"),
    ({"name": "\u202egpj.exe", "days": 30}, "A name cannot hold control or invisible characters"),
    ({"name": "ci\tagent", "days": 30}, "A name cannot hold control or invisible characters"),
    ({"name": "ci-agent", "days": 60}, "Choose an expiry of 7, 30, 90 or 365 days"),
    ({"name": "ci-agent", "days": 0}, "Choose an expiry of 7, 30, 90 or 365 days"),
    ({"name": "ci-agent", "days": None}, "Choose an expiry of 7, 30, 90 or 365 days"),
    ({"name": "ci-agent"}, "Choose an expiry of 7, 30, 90 or 365 days"),
    ({"name": "ci-agent", "days": "30"}, "Choose an expiry of 7, 30, 90 or 365 days"),
    ({"name": "ci-agent", "days": 30.0}, "Choose an expiry of 7, 30, 90 or 365 days"),
    ({"name": "ci-agent", "days": True}, "Choose an expiry of 7, 30, 90 or 365 days"),
])
def test_a_wrong_name_or_expiry_is_refused_and_creates_nothing(hosted, google, data, message):
    ada, _, _ = sign_in(hosted, google)
    before = list_tokens(hosted.store)
    status, _, body = call(hosted, "/api/tokens/create", data, page_headers(hosted, ada))
    assert (status, json.loads(body)) == (400, {"error": message})
    assert list_tokens(hosted.store) == before


def test_a_revoked_token_gets_401_and_leaves_the_list(hosted, google):
    ada, _, _ = sign_in(hosted, google)
    _, _, kept = create(hosted, page_headers(hosted, ada), name="kept")
    _, _, gone = create(hosted, page_headers(hosted, ada), name="gone")
    status, _, body = call(hosted, "/api/tokens/revoke", {"id": gone["id"]}, page_headers(hosted, ada))
    assert (status, json.loads(body)) == (200, {"ok": True})
    assert whoami(hosted, gone["token"]) == 401 and whoami(hosted, kept["token"]) == 200
    assert [row["name"] for row in tokens(hosted, ada)] == ["kept"]
    # A second revoke of the same token changes nothing.
    status, _, body = call(hosted, "/api/tokens/revoke", {"id": gone["id"]}, page_headers(hosted, ada))
    assert (status, json.loads(body)) == (404, {"error": "No active token of yours has this ID"})


def test_a_person_cannot_see_or_revoke_the_token_of_another_person(hosted, google):
    ada, _, _ = sign_in(hosted, google)
    bob, _, _ = sign_in(hosted, google, email="bob@example.com")
    _, _, bobs = create(hosted, page_headers(hosted, bob), name="bob-vm")
    assert tokens(hosted, ada) == []
    unknown = call(hosted, "/api/tokens/revoke", {"id": 999999}, page_headers(hosted, ada))
    other = call(hosted, "/api/tokens/revoke", {"id": bobs["id"]}, page_headers(hosted, ada))
    # The same answer as an unknown ID: it tells nothing about the token of another person.
    assert (other[0], other[2]) == (unknown[0], unknown[2]) == (404, '{"error":"No active token of yours has this ID"}')
    for wrong in ("1", None, True, -1, 0, 2 ** 63):
        status, _, body = call(hosted, "/api/tokens/revoke", {"id": wrong}, page_headers(hosted, ada))
        assert (status, body) == unknown[::2], wrong
    assert whoami(hosted, bobs["token"]) == 200
    assert [row["name"] for row in tokens(hosted, bob)] == ["bob-vm"]


# ---- the session and the CSRF check -----------------------------------------------------


def test_a_bearer_token_alone_cannot_create_or_revoke_a_token(hosted, google):
    ada, _, _ = sign_in(hosted, google)
    _, _, kept = create(hosted, page_headers(hosted, ada), name="kept")
    # A call with the token changes its last use only.
    before = [(row["id"], row["revoked_at"]) for row in list_tokens(hosted.store)]
    bearer = {"Content-Type": "application/json", "X-Taskman": "1", "Origin": hosted.url,
              "Authorization": f"Bearer {kept['token']}"}
    for path, data in (("/api/tokens/create", {"name": "minted", "days": 365}), ("/api/tokens/revoke", {"id": kept["id"]}),
                       ("/api/tokens", {})):
        for headers in (bearer, bearer | {"X-Taskman-CSRF": "0" * 64}):
            status, _, body = call(hosted, path, data, headers)
            assert status == 401 and json.loads(body)["error"]["kind"] == "session", (path, body)
    # The token API has no call that creates a token.
    for path in ("/api/v1/tokens", "/api/v1/tokens/create", "/api/v1/operations/create_token"):
        status, _, _ = call(hosted, path, {"name": "minted", "days": 365}, bearer)
        assert status == 404, path
    assert [(row["id"], row["revoked_at"]) for row in list_tokens(hosted.store)] == before
    assert whoami(hosted, kept["token"]) == 200


def test_a_create_or_revoke_without_the_csrf_check_is_refused(hosted, google):
    ada, _, _ = sign_in(hosted, google)
    _, _, kept = create(hosted, page_headers(hosted, ada), name="kept")
    before = list_tokens(hosted.store)
    without_key = page_headers(hosted, ada)
    del without_key["X-Taskman-CSRF"]
    without_origin = page_headers(hosted, ada)
    del without_origin["Origin"]
    for headers in (without_key, without_origin, page_headers(hosted, ada, origin="https://evil.example"),
                    page_headers(hosted, ada, csrf="0" * 64)):
        for path, data in (("/api/tokens/create", {"name": "minted", "days": 30}), ("/api/tokens/revoke", {"id": kept["id"]})):
            status, _, body = call(hosted, path, data, headers)
            assert status == 403 and json.loads(body)["error"]["kind"] in ("csrf", "stale"), (path, body)
    assert list_tokens(hosted.store) == before and whoami(hosted, kept["token"]) == 200


# ---- the pages -----------------------------------------------------------------------


def test_the_settings_pages_go_through_sign_in_and_come_back(hosted, google):
    for path in ("/settings", "/settings/tokens"):
        secret, headers, _ = sign_in(hosted, google, path)
        assert headers["Location"] == path
        status, headers, text = call(hosted, path, headers=cookie_of(secret))
        assert status == 200 and 'id="token-form"' in text and headers["Cache-Control"] == "no-store"
        assert "script-src 'sha256-" in headers["Content-Security-Policy"]
    assert call(hosted, "/settings/other")[0] == 404


def test_the_old_token_page_redirects_to_settings_and_makes_no_token(hosted, google):
    before = list_tokens(hosted.store)
    status, headers, _ = call(hosted, "/auth/token")
    assert (status, headers["Location"]) == (302, "/settings/tokens")
    # The browser follows to the screen, which signs the person in and comes back.
    secret, headers, _ = sign_in(hosted, google, "/settings/tokens")
    assert headers["Location"] == "/settings/tokens"
    assert list_tokens(hosted.store) == before
    kinds = [row[0] for row in hosted.store.conn.execute("SELECT kind FROM logins")]
    assert kinds == ["ui"]
    assert call(hosted, "/auth/token/revoke", {"page": "x", "token": "1"},
                page_headers(hosted, secret))[0] == 404


def test_the_answer_with_a_new_value_is_not_cached_and_the_log_has_no_value(sqlite_env, google, capsys):
    running = RunningServer(sqlite_env, login=settings(), post_form=google.post_form, log=True)
    try:
        running.logins.settings = dataclasses.replace(running.logins.settings, public_url=running.url)
        secret, _, _ = sign_in(running, google)
        status, headers, created = create(running, page_headers(running, secret))
        assert status == 201
        assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
        assert call(running, "/api/tokens", {}, page_headers(running, secret))[0] == 200
    finally:
        running.stop()
    log = capsys.readouterr().err
    assert '"POST /api/tokens/create HTTP/1.1" 201' in log
    assert created["token"] not in log and created["token"][4:] not in log


# ---- the current CLI ------------------------------------------------------------------


def test_the_cli_login_token_shows_with_its_label_and_the_cli_manages_a_screen_token(hosted, google, monkeypatch):
    verifier = "v" * 50
    authorize = start_cli_login(hosted, verifier)  # the label is "laptop"
    query = google.sign_in(authorize)
    status, headers, _ = call(hosted, "/auth/callback?" + "&".join(f"{k}={v}" for k, v in query.items()))
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    status, _, body = call(hosted, "/auth/exchange", {"code": code, "verifier": verifier})
    assert status == 200 and json.loads(body)["person"] == "ada@example.com"
    ada, _, _ = sign_in(hosted, google)
    _, _, created = create(hosted, page_headers(hosted, ada))
    assert [row["name"] for row in tokens(hosted, ada)] == ["ci-agent", "laptop"]

    monkeypatch.setenv("TASKMAN_SERVER_URL", hosted.url)
    monkeypatch.setenv("TASKMAN_TOKEN", created["token"])
    assert run("auth", "status") == f"Logged in to {hosted.url} as ada@example.com. Token from TASKMAN_TOKEN.\n"
    listing = run("token", "list")
    assert "ci-agent" in listing and "laptop" in listing
    laptop_id = next(row["id"] for row in tokens(hosted, ada) if row["name"] == "laptop")
    assert run("token", "revoke", laptop_id) == f"Revoked token {laptop_id}.\n"
    assert [row["name"] for row in tokens(hosted, ada)] == ["ci-agent"]


# ---- local mode ------------------------------------------------------------------------


def test_local_mode_lists_creates_and_revokes_the_tokens_of_the_local_person(local, monkeypatch):
    status, headers, created = create(local, LOCAL)
    assert status == 201 and headers["Referrer-Policy"] == "no-referrer" and created["token"].startswith("tmn_")
    assert [row["label"] for row in list_tokens(local.store, "Ada")] == ["ci-agent"]
    status, _, body = call(local, "/api/tokens", {}, LOCAL)
    answer = json.loads(body)
    assert answer["person"] == "Ada" and [row["name"] for row in answer["tokens"]] == ["ci-agent"]
    # The token of the test server's own person, Vlad, is not Ada's.
    assert call(local, "/api/tokens/revoke", {"id": local.token_id}, LOCAL)[0] == 404

    monkeypatch.setenv("TASKMAN_SERVER_URL", local.url)
    monkeypatch.setenv("TASKMAN_TOKEN", created["token"])
    assert run("auth", "status") == f"Logged in to {local.url} as Ada. Token from TASKMAN_TOKEN.\n"

    status, _, body = call(local, "/api/tokens/revoke", {"id": created["id"]}, LOCAL)
    assert status == 200, body
    assert whoami(local, created["token"]) == 401 and whoami(local, local.token) == 200
    assert json.loads(call(local, "/api/tokens", {}, LOCAL)[2])["tokens"] == []
    assert "refused the token" in run("auth", "status", code=1)


@pytest.mark.parametrize("headers", [
    LOCAL | {"Host": "example.com"},
    LOCAL | {"Host": "example.com:8080"},
    {"Content-Type": "application/json"},
    {"Content-Type": "application/json", "X-Taskman": "0"},
])
def test_local_mode_refuses_token_calls_from_another_host_or_without_the_header(local, headers):
    _, _, kept = create(local, LOCAL, name="kept")
    before = list_tokens(local.store)
    for path, data in (("/api/tokens/create", {"name": "minted", "days": 30}), ("/api/tokens/revoke", {"id": kept["id"]}),
                       ("/api/tokens", {})):
        status, _, body = call(local, path, data, headers)
        assert (status, json.loads(body)) == (403, {"error": "Only local Taskman UI requests are accepted."}), path
    assert list_tokens(local.store) == before and whoami(local, kept["token"]) == 200


def test_local_mode_serves_the_settings_pages(local):
    for path in ("/settings", "/settings/tokens"):
        status, headers, text = call(local, path)
        assert status == 200 and 'id="token-form"' in text, path
        # The page creates and revokes tokens: no other site may frame it.
        assert headers["Content-Security-Policy"] == "frame-ancestors 'none'" and headers["X-Frame-Options"] == "DENY"
        assert call(local, path, headers={"Host": "example.com"})[0] == 403
    status, headers, _ = call(local, "/auth/token")
    assert (status, headers["Location"]) == (302, "/settings/tokens")


def test_without_local_mode_and_sign_in_the_settings_page_says_that_login_is_not_set_up(server):
    status, _, text = call(server, "/settings/tokens")
    assert status == 200 and "Browser login is not set up" in text
    assert call(server, "/api/tokens/create", {"name": "minted", "days": 30}, LOCAL)[0] == 404
    assert [row["person"] for row in list_tokens(server.store)] == ["Vlad"]
