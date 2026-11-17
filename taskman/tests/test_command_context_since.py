"""`context --since`: an agent reads only what is new on an item, through a local database or a server."""
from __future__ import annotations

import json

import pytest

from tests.sql_helpers import comment, new, run


@pytest.fixture(params=["sqlite_env", "remote_env"])
def env(request):
    return request.getfixturevalue(request.param)


def _since(key, cursor):
    return json.loads(run("context", key, "--since", cursor))


def test_a_read_after_the_cursor_gives_only_new_comments_and_changes(env):
    key = new("Item", body="First text.\n")
    comment(key, "decision", "Chose X.")
    cursor = json.loads(run("context", key))["cursor"]
    assert cursor == "1.1"

    comment(key, "progress", "Half done.", actor="codex")
    run("assign", key, "codex")
    news = _since(key, cursor)
    assert [c["text"] for c in news["comments"]] == ["Half done."]
    assert [(c["field"], c["new"]) for c in news["changes"]] == [("assignee", "codex")]
    assert (news["id"], news["cursor"], news["status"], news["draft"]) == (key, "2.2", "backlog", True)

    # The next read starts at the new cursor.
    assert _since(key, news["cursor"])["comments"] == []


def test_a_description_change_is_a_note_without_the_text(env):
    key = new("Item", body="Old secret text.\n")
    cursor = json.loads(run("context", key))["cursor"]
    run("update", key, "--body-file", "-", input="New long text.\n")
    output = run("context", key, "--since", cursor)
    assert "secret" not in output and "New long text" not in output
    news = json.loads(output)
    assert news["changes"] == [{"field": "description", "author": news["changes"][0]["author"],
                                "at": news["changes"][0]["at"],
                                "note": f"changed; the current revision of the item is {news['revision']}"}]
    assert news["revision"] == 1


def test_a_read_with_nothing_new_is_one_line(env):
    key = new("Item")
    for _ in range(3):
        comment(key, "progress", "Step done.")
    cursor = json.loads(run("context", key))["cursor"]
    output = run("context", key, "--since", cursor)
    assert output.count("\n") == 1
    assert json.loads(output) == {"id": key, "cursor": cursor, "revision": 3, "status": "backlog", "draft": True,
                                  "comments": [], "changes": []}


@pytest.mark.parametrize("cursor", ["", "abc", "1", "1.2.3", "-1.0", "9.0", "0.9", "00.0", "0.0\n", " 0.0"])
def test_an_unknown_or_malformed_cursor_is_a_usage_error(env, cursor):
    key = new("Item")
    assert "unknown cursor" in run("context", key, "--since", cursor, code=2)


def test_an_unknown_key_with_a_cursor_is_not_found(env):
    assert "item not found: TM-9" in run("context", "TM-9", "--since", "0.0", code=1)


def test_a_refresh_stays_small_on_an_item_with_a_long_history(env):
    key = new("Item", body="Spec.\n" * 50)
    for number in range(20):
        comment(key, "progress", f"Step {number} done with a description of what changed and why. " * 3)
    cursor = json.loads(run("context", key))["cursor"]
    full = len(run("context", key))
    quiet = len(run("context", key, "--since", cursor))
    comment(key, "progress", "One more step.")
    one = len(run("context", key, "--since", cursor))
    print(f"full context: {full} bytes; refresh with no news: {quiet} bytes; refresh with one comment: {one} bytes")
    assert quiet < 200 and one < 600 and full > 20 * one
