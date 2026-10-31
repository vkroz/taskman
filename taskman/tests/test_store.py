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
