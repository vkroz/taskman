"""Postgres-backed task storage."""
from __future__ import annotations

import pytest

from taskman.db import connect
from taskman.store import TaskmanError, complete_task, create_task, pull_task, ready_tasks, set_dependencies


def test_create_list_and_reject_a_child_of_a_leaf(database):
    with connect(database) as conn:
        epic = create_task(conn, "Epic", "Outcome", "epic", None)
        child = create_task(conn, "Child", "Do the work", "task", epic["id"])
        assert child["parent_id"] == epic["id"]
        assert child["status"] == "backlog"
        with pytest.raises(TaskmanError) as exc:
            create_task(conn, "Nope", "", "task", child["id"])
        assert exc.value.status == 400


def test_dependencies_block_ready_until_they_are_done(database):
    with connect(database) as conn:
        first = create_task(conn, "First", "", "task", None)
        second = create_task(conn, "Second", "", "task", None)
        set_dependencies(conn, second["id"], [first["id"]])
        assert [task["id"] for task in ready_tasks(conn)] == [first["id"]]
        with pytest.raises(TaskmanError):
            set_dependencies(conn, first["id"], [second["id"]])
        complete_task(conn, first["id"], "done", "codex", "s")
        assert [task["id"] for task in ready_tasks(conn)] == [second["id"]]


def test_completion_is_idempotent_for_the_same_session(database):
    with connect(database) as conn:
        task = create_task(conn, "Finish", "", "task", None)
        complete_task(conn, task["id"], "shipped", "claude-code", "sess")
        complete_task(conn, task["id"], "shipped again", "claude-code", "sess")
        count = conn.execute("SELECT count(*) AS n FROM events WHERE kind = 'completion'").fetchone()["n"]
        assert count == 1
        assert conn.execute("SELECT status FROM tasks WHERE id = %s", (task["id"],)).fetchone()["status"] == "done"


def test_pull_marks_backlog_work_active(database):
    with connect(database) as conn:
        task = create_task(conn, "Claim", "Body", "task", None)
        payload = pull_task(conn, task["id"], "codex", "session-1")
        assert payload["task"]["status"] == "active"
        assert payload["events"][0]["kind"] == "pull"
        assert payload["events"][0]["agent"] == "codex"


def test_the_same_commit_sha_is_stored_once(database):
    with connect(database) as conn:
        task = create_task(conn, "Commit", "", "task", None)
        from taskman.store import add_event

        first, created = add_event(conn, task["id"], "commit", agent="git", commit_sha="abc123", message="one")
        second, replayed = add_event(
            conn, task["id"], "commit", agent="claude-code", commit_sha="abc123", message="again"
        )
        assert created is True
        assert replayed is False
        assert first["id"] == second["id"]
        count = conn.execute("SELECT count(*) AS n FROM events WHERE kind = 'commit'").fetchone()["n"]
        assert count == 1


def test_a_blank_agent_is_rejected_for_every_event(database):
    with connect(database) as conn:
        task = create_task(conn, "Named", "", "task", None)
        from taskman.store import add_event

        for kind in ("pull", "commit", "pull_request", "completion", "note"):
            with pytest.raises(TaskmanError) as exc:
                add_event(conn, task["id"], kind, agent="  ", commit_sha="abc", pr_url="https://example.com/1")
            assert exc.value.status == 400
            assert exc.value.message == "Agent is required: name who authored these changes."
        count = conn.execute("SELECT count(*) AS n FROM events").fetchone()["n"]
        assert count == 0


def _in_thread(fn):
    import threading

    result = {}

    def run():
        try:
            result["value"] = fn()
        except Exception as exc:  # noqa: BLE001 — the test inspects it
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def test_concurrent_dependency_updates_cannot_create_a_cycle(database):
    with connect(database) as conn:
        first = create_task(conn, "First", "", "task", None)["id"]
        second = create_task(conn, "Second", "", "task", None)["id"]

    def reverse():
        with connect(database) as other:
            return set_dependencies(other, second, [first])

    with connect(database) as conn:
        set_dependencies(conn, first, [second])
        thread, result = _in_thread(reverse)
        thread.join(0.5)
        # The second update waits for this transaction instead of reading a stale graph.
        assert thread.is_alive()
        conn.commit()
    thread.join(5)
    assert not thread.is_alive()
    assert isinstance(result.get("error"), TaskmanError)
    assert "cycle" in result["error"].message
    with connect(database) as conn:
        edges = conn.execute("SELECT task_id, depends_on FROM task_dependencies").fetchall()
    assert [(row["task_id"], row["depends_on"]) for row in edges] == [(first, second)]


def test_concurrent_identical_completions_store_one_event(database):
    from taskman.store import add_event

    with connect(database) as conn:
        task = create_task(conn, "Finish", "", "task", None)["id"]

    def again():
        with connect(database) as other:
            return add_event(other, task, "completion", agent="codex", session="s", message="done")

    with connect(database) as conn:
        _event, created = add_event(conn, task, "completion", agent="codex", session="s", message="done")
        assert created is True
        thread, result = _in_thread(again)
        thread.join(0.5)
        assert thread.is_alive()
        conn.commit()
    thread.join(5)
    assert not thread.is_alive()
    assert "error" not in result, result.get("error")
    assert result["value"][1] is False
    with connect(database) as conn:
        count = conn.execute("SELECT count(*) AS n FROM events WHERE kind = 'completion'").fetchone()["n"]
    assert count == 1


def test_migrate_can_run_twice(database):
    from taskman.db import migrate

    migrate(database)
    migrate(database)
