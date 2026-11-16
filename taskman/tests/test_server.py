"""The Taskman server: tokens, reads, operations and the errors it reports."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from taskman.server import MAX_REQUEST_BYTES
from taskman.storage.accounts import create_token, list_tokens, revoke_token


def call(server, method: str, path: str, payload=None, token: str | None = "", raw: bytes | None = None):
    """One request. Returns the status and the JSON answer. ``token=""`` sends the server's token."""
    body = raw if raw is not None else (None if payload is None else json.dumps(payload).encode())
    request = urllib.request.Request(server.url + path, data=body, method=method)
    token = server.token if token == "" else token
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def run(server, operation: str, arguments: dict, actor: str | None = None, token: str | None = ""):
    return call(server, "POST", f"/api/v1/operations/{operation}", {"actor": actor, "arguments": arguments}, token)


def create(server, title: str = "Task", **more) -> str:
    status, answer = run(server, "create", {"title": title, "item_type": "task", "parent_id": None,
                                            "project": "TM", **more})
    assert status == 200, answer
    return answer["item"]["id"]


# ---- tokens ---------------------------------------------------------------


def test_health_checks_need_no_token(server):
    assert call(server, "GET", "/healthz", token=None) == (200, {"ok": True})
    assert call(server, "GET", "/readyz", token=None) == (200, {"ok": True})


@pytest.mark.parametrize("token", [None, "tmn_wrong", "not-a-token"])
def test_a_call_without_a_valid_token_gets_401_and_changes_nothing(server, token):
    status, answer = call(server, "GET", "/api/v1/items", token=token)
    assert status == 401 and answer["error"]["kind"] == "token"
    status, answer = run(server, "create", {"title": "T", "item_type": "task", "parent_id": None, "project": "TM"},
                         token=token)
    assert status == 401
    assert server.store.items() == []


def test_a_revoked_token_fails_on_the_next_call(server):
    assert call(server, "GET", "/api/v1/items")[0] == 200
    assert revoke_token(server.store, server.token_id) is True
    assert call(server, "GET", "/api/v1/items")[0] == 401
    assert revoke_token(server.store, server.token_id) is False


def test_an_expired_token_is_refused(server):
    token_id, token = create_token(server.store, "Vlad", days=1)
    assert call(server, "GET", "/api/v1/items", token=token)[0] == 200
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    server.store.conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?", (past, token_id))
    assert call(server, "GET", "/api/v1/items", token=token)[0] == 401


def test_the_database_keeps_no_token_text(server):
    rows = server.store.conn.execute("SELECT token_hash, label FROM tokens").fetchall()
    assert rows and all(server.token not in str(row) for row in rows)
    assert all("token_hash" not in row and server.token not in str(row) for row in list_tokens(server.store))


def test_a_token_use_is_recorded(server):
    call(server, "GET", "/api/v1/items")
    assert list_tokens(server.store)[0]["last_used_at"]


# ---- a person's own tokens ------------------------------------------------


def test_a_person_lists_only_their_own_tokens_without_values(server):
    second_id, second = create_token(server.store, "Vlad", "laptop", days=90)
    other_id, other = create_token(server.store, "Ada", "build-vm", days=90)
    status, answer = call(server, "GET", "/api/v1/tokens")
    assert status == 200
    assert [row["id"] for row in answer["tokens"]] == [server.token_id, second_id]
    assert set(answer["tokens"][1]) == {"id", "label", "created_at", "expires_at", "last_used_at", "revoked_at"}
    assert answer["tokens"][1]["label"] == "laptop" and answer["tokens"][1]["revoked_at"] is None
    assert answer["tokens"][0]["last_used_at"] and answer["tokens"][0]["expires_at"] is None
    text = json.dumps(answer)
    hashes = [row[0] for row in server.store.conn.execute("SELECT token_hash FROM tokens").fetchall()]
    for secret in [server.token, second, other, *hashes]:
        assert secret not in text
    assert "Ada" not in text and "build-vm" not in text
    assert [row["id"] for row in call(server, "GET", "/api/v1/tokens", token=other)[1]["tokens"]] == [other_id]
    assert call(server, "GET", "/api/v1/tokens", token=None)[0] == 401


def test_a_person_revokes_an_own_token_and_it_stops_working_at_once(server):
    second_id, second = create_token(server.store, "Vlad", "laptop", days=90)
    assert call(server, "GET", "/api/v1/whoami", token=second)[0] == 200
    assert call(server, "POST", f"/api/v1/tokens/{second_id}/revoke", {}) == (200, {"revoked": second_id})
    assert call(server, "GET", "/api/v1/whoami", token=second)[0] == 401
    revoked = call(server, "GET", "/api/v1/tokens")[1]["tokens"][1]
    assert revoked["id"] == second_id and revoked["revoked_at"]
    status, answer = call(server, "POST", f"/api/v1/tokens/{second_id}/revoke", {})
    assert status == 404 and answer["error"]["message"] == f"no active token of yours has the ID {second_id}"


def test_a_person_revokes_the_token_in_use_and_the_next_request_is_refused(server):
    assert call(server, "POST", f"/api/v1/tokens/{server.token_id}/revoke", {})[0] == 200
    status, answer = call(server, "GET", "/api/v1/items")
    assert status == 401 and answer["error"]["kind"] == "token"


def test_nobody_revokes_the_token_of_another_person(server):
    other_id, other = create_token(server.store, "Ada", days=90)
    status, answer = call(server, "POST", f"/api/v1/tokens/{other_id}/revoke", {})
    unknown = call(server, "POST", "/api/v1/tokens/999999/revoke", {})
    # Another person's token and a token that does not exist get the same answer.
    assert (status, answer["error"]["message"]) == (404, f"no active token of yours has the ID {other_id}")
    assert unknown[0] == 404 and unknown[1]["error"]["message"] == "no active token of yours has the ID 999999"
    assert call(server, "GET", "/api/v1/whoami", token=other) == (200, {"person": "Ada"})
    assert list_tokens(server.store, "Ada")[0]["revoked_at"] is None
    assert call(server, "POST", f"/api/v1/tokens/{other_id}/revoke", {}, token=None)[0] == 401
    assert call(server, "POST", f"/api/v1/tokens/{other_id}/revoke", {}, token="tmn_wrong")[0] == 401
    assert call(server, "POST", "/api/v1/tokens/x/revoke", {})[0] == 404


# ---- reads ----------------------------------------------------------------


def test_reads_return_items_order_projects_document_and_activity(server):
    key = create(server, "First")
    assert [item["id"] for item in call(server, "GET", "/api/v1/items")[1]["items"]] == [key]
    assert call(server, "GET", f"/api/v1/items/{key}")[1]["item"]["metadata"]["title"] == "First"
    assert call(server, "GET", "/api/v1/order")[1]["order"] == {"root:TM": [key]}
    assert call(server, "GET", "/api/v1/projects")[1]["projects"] == [{"key": "TM", "name": "TM"}]
    assert "title: First" in call(server, "GET", f"/api/v1/items/{key}/document")[1]["document"]
    assert call(server, "GET", f"/api/v1/items/{key}/activity")[1]["activity"][0]["field"] == "created"


def test_whoami_names_the_person_of_the_token(server):
    assert call(server, "GET", "/api/v1/whoami") == (200, {"person": "Vlad"})
    assert call(server, "GET", "/api/v1/whoami", token=None)[0] == 401


def test_reads_of_unknown_things(server):
    assert call(server, "GET", "/api/v1/items/TM-9") == (200, {"item": None})
    assert call(server, "GET", "/api/v1/items/TM-9/document")[0] == 404
    assert call(server, "GET", "/api/v1/nothing")[0] == 404
    assert call(server, "GET", "/elsewhere", token=None)[0] == 404


# ---- operations -----------------------------------------------------------


def test_the_person_of_the_token_is_the_author(server):
    key = create(server)
    assert server.store.get(key).metadata["created_by"] == "Vlad"
    run(server, "assign", {"item_id": key, "assignee": "codex"}, actor="claude")
    assert server.store.activity(key)[-1]["author"] == "claude for Vlad"


def test_a_comment_cannot_name_another_person_as_author(server):
    key = create(server)
    status, answer = run(server, "record", {"item_id": key, "kind": "decision", "actor": "Someone Else",
                                            "text": "Chose X."})
    assert status == 200
    assert answer["item"]["metadata"]["journal"][0]["actor"] == "Someone Else for Vlad"
    run(server, "record", {"item_id": key, "kind": "decision", "actor": "Vlad", "text": "Chose Y."})
    assert server.store.get(key).metadata["journal"][1]["actor"] == "Vlad"


def test_an_actor_name_has_a_length_limit(server):
    key = create(server)
    status, answer = run(server, "assign", {"item_id": key, "assignee": "codex"}, actor="a" * 36)
    assert status == 400 and "actor name" in answer["error"]["message"]


def test_a_rule_error_is_reported_and_changes_nothing(server):
    key = create(server)
    before = server.store.items()
    status, answer = run(server, "close", {"item_id": key})
    assert status == 422 and answer["error"]["kind"] == "lifecycle" and answer["error"]["usage"] is True
    status, answer = run(server, "create", {"title": "Child", "item_type": "task", "parent_id": key})
    assert status == 422 and "usage" not in answer["error"]
    assert server.store.items() == before


def test_a_request_for_the_current_state_is_no_change(server):
    key = create(server)
    run(server, "assign", {"item_id": key, "assignee": "codex"})
    status, answer = run(server, "assign", {"item_id": key, "assignee": "codex"})
    assert status == 200 and "No change" in answer["no_change"]


def test_reorder_returns_the_new_order(server):
    first, second = create(server, "First"), create(server, "Second")
    assert run(server, "reorder_item", {"item_id": second, "position": 1}) == (200, {"order": [second, first]})
    status, answer = run(server, "reorder_item", {"item_id": second, "position": 9})
    assert status == 422 and answer["error"]["kind"] == "value"


def test_discard_returns_no_item(server):
    key = create(server)
    assert run(server, "discard", {"item_id": key}) == (200, {})
    assert server.store.items() == []


@pytest.mark.parametrize(
    ("operation", "arguments", "message"),
    [
        ("close", {"item_id": "TM-1", "extra": 1}, "unknown argument: extra"),
        ("close", {}, "missing argument: item_id"),
        ("close", {"item_id": 5}, "wrong type for argument: item_id"),
        ("label", {"item_id": "TM-1", "add": ["ok", 5], "remove": []}, "wrong type for argument: add"),
        ("reorder_item", {"item_id": "TM-1", "position": True}, "wrong type for argument: position"),
        ("close", ["TM-1"], "arguments must be an object"),
    ],
)
def test_wrong_arguments_get_400(server, operation, arguments, message):
    status, answer = run(server, operation, arguments)
    assert status == 400 and answer["error"]["message"] == message


def test_unknown_operation_and_bad_bodies(server):
    assert run(server, "save", {})[0] == 404
    assert call(server, "POST", "/api/v1/items", {})[0] == 404
    assert call(server, "POST", "/api/v1/operations/close", raw=b"{not json")[0] == 400
    assert call(server, "POST", "/api/v1/operations/close", raw=b"[1]")[0] == 400
    assert call(server, "POST", "/api/v1/operations/close", raw=b"")[0] == 400


def test_a_request_over_the_size_limit_gets_413(server):
    status, _ = run(server, "create", {"title": "T", "item_type": "task", "parent_id": None, "project": "TM",
                                       "body": "x" * MAX_REQUEST_BYTES})
    assert status == 413
    assert server.store.items() == []


def test_a_failure_inside_the_server_gives_no_details(server, monkeypatch):
    monkeypatch.setattr(server.store, "items", lambda: 1 / 0)
    status, answer = call(server, "GET", "/api/v1/items")
    assert status == 500 and answer == {"error": {"kind": "server", "message": "The Taskman server failed."}}


def test_not_ready_when_the_database_fails(server, monkeypatch):
    monkeypatch.setattr(server.store, "projects", lambda: 1 / 0)
    assert call(server, "GET", "/readyz", token=None)[0] == 503
    assert call(server, "GET", "/healthz", token=None)[0] == 200


# ---- both SQL dialects ----------------------------------------------------


def test_the_server_works_over_each_database(sql_settings):
    """The same calls pass on SQLite and on Postgres; a caller cannot tell which one the server uses."""
    from taskman.storage.schema import upgrade
    from taskman.storage.sql import connect
    from tests.conftest import RunningServer

    conn, dialect = connect(sql_settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    running = RunningServer(sql_settings)
    try:
        key = create(running, "Task")
        assert call(running, "GET", f"/api/v1/items/{key}")[1]["item"]["metadata"]["created_by"] == "Vlad"
        assert call(running, "GET", "/api/v1/items", token="tmn_wrong")[0] == 401
        assert revoke_token(running.store, running.token_id) is True
        assert call(running, "GET", "/api/v1/items")[0] == 401
        assert [row["person"] for row in list_tokens(running.store)] == ["Vlad"]
    finally:
        running.stop()


def test_a_bugfix_is_created_and_takes_no_children(server):
    key = create(server, "Fix crash", item_type="bugfix")
    assert call(server, "GET", f"/api/v1/items/{key}")[1]["item"]["metadata"]["type"] == "bugfix"
    status, answer = run(server, "create", {"title": "Child", "item_type": "task", "parent_id": key})
    assert status == 422 and "bugfix = leaf invariant" in answer["error"]["message"]
    assert run(server, "convert", {"item_id": key, "target_type": "task"})[0] == 200
    assert run(server, "convert", {"item_id": key, "target_type": "bugfix"})[0] == 200


def test_an_unknown_type_is_a_rule_error(server):
    key = create(server)
    for operation, arguments in (("create", {"title": "Bug", "item_type": "bug", "parent_id": None, "project": "TM"}),
                                 ("convert", {"item_id": key, "target_type": "bug"})):
        status, answer = run(server, operation, arguments)
        assert status == 422 and answer["error"]["usage"] is True
        assert answer["error"]["message"] == "the type must be one of epic, feat, task, bugfix, got: 'bug'"
    assert [item.type for item in server.store.items()] == ["task"]

def test_the_server_reconnects_after_the_database_ends_its_connection(sql_settings):
    """After a database restart, /readyz, reads and writes work again in the same process."""
    from taskman.storage.schema import upgrade
    from taskman.storage.sql import connect
    from tests.conftest import RunningServer, end_connection

    conn, dialect = connect(sql_settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    running = RunningServer(sql_settings)
    try:
        key = create(running, "Before")
        end_connection(running.store)
        assert call(running, "GET", "/readyz", token=None) == (200, {"ok": True})
        end_connection(running.store)
        assert call(running, "GET", f"/api/v1/items/{key}")[1]["item"]["metadata"]["title"] == "Before"
        end_connection(running.store)
        create(running, "After")
        assert [item["metadata"]["title"] for item in call(running, "GET", "/api/v1/items")[1]["items"]] == [
            "Before", "After"]
    finally:
        running.stop()


def test_not_ready_while_the_database_is_away_and_ready_when_it_is_back(server):
    """A reconnect that fails answers 503; the next request after the database returns reconnects."""
    from tests.conftest import end_connection

    path = server.store.conn.execute("PRAGMA database_list").fetchone()[2]
    away = path + ".away"
    end_connection(server.store)
    os.rename(path, away)
    try:
        assert call(server, "GET", "/readyz", token=None)[0] == 503
        assert call(server, "GET", "/api/v1/items")[0] == 500
    finally:
        os.rename(away, path)
    assert call(server, "GET", "/readyz", token=None) == (200, {"ok": True})
    assert call(server, "GET", "/api/v1/items") == (200, {"items": []})
