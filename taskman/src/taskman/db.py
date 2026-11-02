"""Postgres connection, readiness, and schema migration.

The server talks only to this database. It does not read or write a git checkout.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id BIGSERIAL PRIMARY KEY,
        item_type TEXT NOT NULL CHECK (item_type IN ('task', 'feat', 'epic')),
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN ('backlog', 'active', 'done')),
        parent_id BIGINT REFERENCES tasks (id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id BIGINT NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
        depends_on BIGINT NOT NULL REFERENCES tasks (id),
        PRIMARY KEY (task_id, depends_on),
        CHECK (task_id <> depends_on)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id BIGSERIAL PRIMARY KEY,
        task_id BIGINT NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('pull', 'commit', 'pull_request', 'completion', 'note')),
        agent TEXT NOT NULL DEFAULT '',
        session TEXT NOT NULL DEFAULT '',
        commit_sha TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '',
        pr_url TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS events_task_created_idx ON events (task_id, created_at DESC, id DESC)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS events_commit_sha_once
    ON events (task_id, kind, commit_sha)
    WHERE kind = 'commit' AND commit_sha <> ''
    """,
)


def database_url() -> str:
    url = os.environ.get("TASKMAN_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("TASKMAN_DATABASE_URL is not set.")
    return url


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(url or database_url(), row_factory=dict_row, connect_timeout=3) as conn:
        yield conn


def ping(url: str | None = None) -> None:
    with connect(url) as conn:
        conn.execute("SELECT 1")


def wait_for_database(url: str, attempts: int = 30, delay: float = 1.0) -> None:
    """Retry until Postgres accepts a connection. Used at process start."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ping(url)
            return
        except Exception as exc:  # noqa: BLE001 — readiness covers auth, DNS, and refusal
            last = exc
            if attempt == 1 or attempt == attempts or attempt % 5 == 0:
                print(f"Waiting for Postgres ({attempt}/{attempts}): {exc}", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"Postgres is not ready: {last}")


# Advisory lock key that serializes concurrent migrations from several processes.
_MIGRATE_LOCK = 0x74616D67  # "tamg"


def migrate(url: str | None = None) -> None:
    """Create tables if they are missing. ``taskman serve`` runs this once before gunicorn starts.

    A transaction-scoped advisory lock serializes concurrent runs, for example
    two app containers that start together.
    """
    with connect(url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATE_LOCK,))
        for statement in STATEMENTS:
            conn.execute(statement)
