"""Task and event storage. Postgres is the only system of record."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.errors import UniqueViolation

from taskman.db import connect

TYPES = {"task", "feat", "epic"}
STATUSES = {"backlog", "active", "done"}
EVENT_KINDS = {"pull", "commit", "pull_request", "completion", "note"}

# Transaction-scoped advisory lock that serializes dependency changes, so two
# concurrent updates cannot each pass the cycle check and together form a cycle.
_DEPENDENCY_LOCK = 0x74617364  # "tasd"


class TaskmanError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _task(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "type": row["item_type"],
        "title": row["title"],
        "body": row["body"],
        "status": row["status"],
        "parent_id": row["parent_id"],
        "created_at": _ts(row["created_at"]),
        "updated_at": _ts(row["updated_at"]),
    }


def _event(row: dict) -> dict:
    detail = row["pr_url"] or ""
    if not detail and row["commit_sha"]:
        detail = f"{row['commit_sha'][:12]} {row['message']}".strip()
    if not detail:
        detail = row["message"]
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "kind": row["kind"],
        "agent": row["agent"],
        "session": row["session"],
        "commit_sha": row["commit_sha"],
        "message": row["message"],
        "pr_url": row["pr_url"],
        "detail": detail,
        "created_at": _ts(row["created_at"]),
    }


def _require_task(conn: Connection, task_id: int, *, lock: bool = False) -> dict:
    """Return the task row. ``lock`` holds it FOR UPDATE until the transaction ends."""
    query = "SELECT * FROM tasks WHERE id = %s" + (" FOR UPDATE" if lock else "")
    row = conn.execute(query, (task_id,)).fetchone()
    if row is None:
        raise TaskmanError(404, f"task not found: {task_id}")
    return row


def create_task(conn: Connection, title: str, body: str, item_type: str, parent_id: int | None) -> dict:
    title = title.strip()
    if not title or len(title) > 200:
        raise TaskmanError(400, "Enter a title of 1–200 characters.")
    if item_type not in TYPES:
        raise TaskmanError(400, "Type must be task, feat, or epic.")
    if not isinstance(body, str):
        raise TaskmanError(400, "Body must be text.")
    if parent_id is not None:
        parent = _require_task(conn, parent_id)
        if parent["item_type"] == "task":
            raise TaskmanError(400, "A task is a leaf and cannot have children.")
    row = conn.execute(
        """
        INSERT INTO tasks (item_type, title, body, status, parent_id)
        VALUES (%s, %s, %s, 'backlog', %s)
        RETURNING *
        """,
        (item_type, title, body, parent_id),
    ).fetchone()
    return _task(row)


def list_tasks(conn: Connection, status: str | None = None) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise TaskmanError(400, "Status must be backlog, active, or done.")
    if status is None:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks WHERE status = %s ORDER BY id", (status,)).fetchall()
    return [_task(row) for row in rows]


def get_task(conn: Connection, task_id: int) -> dict:
    return _task(_require_task(conn, task_id))


def update_task(
    conn: Connection,
    task_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    status: str | None = None,
) -> dict:
    current = _require_task(conn, task_id)
    new_title = current["title"] if title is None else title.strip()
    new_body = current["body"] if body is None else body
    new_status = current["status"] if status is None else status
    if not new_title or len(new_title) > 200:
        raise TaskmanError(400, "Enter a title of 1–200 characters.")
    if not isinstance(new_body, str):
        raise TaskmanError(400, "Body must be text.")
    if new_status not in STATUSES:
        raise TaskmanError(400, "Status must be backlog, active, or done.")
    row = conn.execute(
        """
        UPDATE tasks
        SET title = %s, body = %s, status = %s, updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (new_title, new_body, new_status, task_id),
    ).fetchone()
    return _task(row)


def _edges(conn: Connection) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = {}
    for row in conn.execute("SELECT task_id, depends_on FROM task_dependencies").fetchall():
        graph.setdefault(row["task_id"], set()).add(row["depends_on"])
    return graph


def _reaches(graph: dict[int, set[int]], start: int, target: int) -> bool:
    seen = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return False


def set_dependencies(conn: Connection, task_id: int, depends_on: list[int]) -> list[int]:
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_DEPENDENCY_LOCK,))
    _require_task(conn, task_id)
    unique: list[int] = []
    for dep in depends_on:
        if dep == task_id:
            raise TaskmanError(400, "A task cannot depend on itself.")
        if dep not in unique:
            _require_task(conn, dep)
            unique.append(dep)
    graph = _edges(conn)
    graph[task_id] = set(unique)
    if any(_reaches(graph, dep, task_id) for dep in unique):
        raise TaskmanError(400, "That dependency would create a cycle.")
    conn.execute("DELETE FROM task_dependencies WHERE task_id = %s", (task_id,))
    for dep in unique:
        conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on) VALUES (%s, %s)",
            (task_id, dep),
        )
    return unique


def dependency_ids(conn: Connection, task_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT depends_on FROM task_dependencies WHERE task_id = %s ORDER BY depends_on",
        (task_id,),
    ).fetchall()
    return [row["depends_on"] for row in rows]


def ready_tasks(conn: Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM tasks AS task
        WHERE task.status IN ('backlog', 'active')
          AND NOT EXISTS (
            SELECT 1
            FROM task_dependencies AS link
            JOIN tasks AS dep ON dep.id = link.depends_on
            WHERE link.task_id = task.id AND dep.status <> 'done'
          )
        ORDER BY task.id
        """
    ).fetchall()
    return [_task(row) for row in rows]


def add_event(
    conn: Connection,
    task_id: int,
    kind: str,
    *,
    agent: str = "",
    session: str = "",
    commit_sha: str = "",
    message: str = "",
    pr_url: str = "",
) -> tuple[dict, bool]:
    """Insert an event. Returns the stored row and whether this call inserted it.

    A blank agent is rejected for every kind. Two posts store nothing new and
    the second value is False: a commit whose sha is already stored for the
    task, and a completion that repeats the latest completion for the same
    agent and session. The HTTP route answers 201 for a new row and 200 for
    that replay. 200, not 409, so a retry stays a success for the CLI and hooks.

    The task row is locked FOR UPDATE, so concurrent posts for one task run
    the duplicate check one at a time and cannot both insert.
    """
    _require_task(conn, task_id, lock=True)
    if kind not in EVENT_KINDS:
        raise TaskmanError(400, "Unknown event kind.")
    message = (message or "")[:4000]
    agent = (agent or "").strip()[:200]
    session = (session or "")[:200]
    commit_sha = (commit_sha or "")[:80]
    pr_url = (pr_url or "")[:500]
    if not agent:
        raise TaskmanError(400, "Agent is required.")
    latest = conn.execute(
        """
        SELECT * FROM events
        WHERE task_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    if (
        latest
        and latest["kind"] == kind == "completion"
        and latest["agent"] == agent
        and latest["session"] == session
    ):
        return _event(latest), False
    if kind == "commit" and commit_sha:
        existing = conn.execute(
            """
            SELECT * FROM events
            WHERE task_id = %s AND kind = 'commit' AND commit_sha = %s
            """,
            (task_id, commit_sha),
        ).fetchone()
        if existing:
            return _event(existing), False
    try:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO events (task_id, kind, agent, session, commit_sha, message, pr_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (task_id, kind, agent, session, commit_sha, message, pr_url),
            ).fetchone()
            conn.execute("UPDATE tasks SET updated_at = now() WHERE id = %s", (task_id,))
    except UniqueViolation:
        row = conn.execute(
            """
            SELECT * FROM events
            WHERE task_id = %s AND kind = 'commit' AND commit_sha = %s
            """,
            (task_id, commit_sha),
        ).fetchone()
        return _event(row), False
    return _event(row), True


def list_events(conn: Connection, task_id: int) -> list[dict]:
    _require_task(conn, task_id)
    rows = conn.execute(
        "SELECT * FROM events WHERE task_id = %s ORDER BY created_at, id",
        (task_id,),
    ).fetchall()
    return [_event(row) for row in rows]


def context(conn: Connection, task_id: int) -> dict:
    task_row = _require_task(conn, task_id)
    parent = None
    if task_row["parent_id"] is not None:
        parent = _task(conn.execute("SELECT * FROM tasks WHERE id = %s", (task_row["parent_id"],)).fetchone())
    children = [
        _task(row)
        for row in conn.execute("SELECT * FROM tasks WHERE parent_id = %s ORDER BY id", (task_id,)).fetchall()
    ]
    deps = []
    for dep_id in dependency_ids(conn, task_id):
        deps.append(get_task(conn, dep_id))
    return {
        "task": _task(task_row),
        "parent": parent,
        "children": children,
        "dependencies": deps,
        "events": list_events(conn, task_id),
    }


def pull_task(conn: Connection, task_id: int, agent: str, session: str) -> dict:
    row = _require_task(conn, task_id)
    if row["status"] == "backlog":
        conn.execute(
            "UPDATE tasks SET status = 'active', updated_at = now() WHERE id = %s",
            (task_id,),
        )
    add_event(conn, task_id, "pull", agent=agent, session=session, message="pulled into a working copy")
    return context(conn, task_id)


def complete_task(conn: Connection, task_id: int, message: str, agent: str, session: str) -> dict:
    _require_task(conn, task_id)
    event, _created = add_event(
        conn, task_id, "completion", agent=agent, session=session, message=message or "completed"
    )
    task = update_task(conn, task_id, status="done")
    return {"task": task, "event": event}


def dashboard(conn: Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT task.*,
               event.kind AS event_kind,
               event.agent AS event_agent,
               event.session AS event_session,
               event.commit_sha AS event_commit_sha,
               event.message AS event_message,
               event.pr_url AS event_pr_url,
               event.created_at AS event_created_at
        FROM tasks AS task
        LEFT JOIN LATERAL (
            SELECT * FROM events
            WHERE task_id = task.id
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        ) AS event ON TRUE
        ORDER BY event.created_at DESC NULLS LAST, task.id DESC
        """
    ).fetchall()
    board = []
    for row in rows:
        event = None
        if row["event_kind"]:
            event = _event(
                {
                    "id": None,
                    "task_id": row["id"],
                    "kind": row["event_kind"],
                    "agent": row["event_agent"],
                    "session": row["event_session"],
                    "commit_sha": row["event_commit_sha"],
                    "message": row["event_message"],
                    "pr_url": row["event_pr_url"],
                    "created_at": row["event_created_at"],
                }
            )
        board.append(
            {
                "id": row["id"],
                "type": row["item_type"],
                "title": row["title"],
                "status": row["status"],
                "agent": event["agent"] if event else "",
                "session": event["session"] if event else "",
                "last_event": event["kind"] if event else "",
                "last_activity": event["created_at"] if event else None,
                "detail": event["detail"] if event else "",
            }
        )
    return board


def with_connection(fn, *args, **kwargs) -> Any:
    with connect() as conn:
        return fn(conn, *args, **kwargs)
