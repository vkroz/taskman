"""Versioned SQL schema for the work-item model in ``docs/data-model.md``.

Each migration has a version number. ``upgrade`` applies the migrations
above the database's recorded version, in order, in one transaction, and
records each version in ``schema_versions``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from taskman.storage.sql import Dialect, transaction
from taskman.storage.store import StorageConfigError


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


# Column types that differ between the dialects.
_TYPES = {
    "sqlite": {"pk": "INTEGER PRIMARY KEY AUTOINCREMENT", "bigint": "INTEGER", "real": "REAL"},
    "postgres": {"pk": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY", "bigint": "BIGINT", "real": "DOUBLE PRECISION"},
}

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "work item model",
        (
            """
            CREATE TABLE projects (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
                next_number {bigint} NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE items (
                id {pk},
                project TEXT NOT NULL REFERENCES projects (key),
                number {bigint} NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('epic', 'feat', 'task')),
                title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('backlog', 'active', 'done')),
                draft INTEGER NOT NULL DEFAULT 1 CHECK (draft IN (0, 1)),
                parent_id {bigint} REFERENCES items (id),
                rank {real} NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0,
                assignee TEXT CHECK (assignee IS NULL OR length(assignee) BETWEEN 1 AND 100),
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                UNIQUE (project, number)
            )
            """,
            "CREATE INDEX items_project_status ON items (project, status)",
            "CREATE INDEX items_parent ON items (parent_id)",
            """
            CREATE TABLE labels (
                item_id {bigint} NOT NULL REFERENCES items (id) ON DELETE CASCADE,
                label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 50),
                PRIMARY KEY (item_id, label)
            )
            """,
            """
            CREATE TABLE dependencies (
                item_id {bigint} NOT NULL REFERENCES items (id) ON DELETE CASCADE,
                depends_on {bigint} NOT NULL REFERENCES items (id),
                PRIMARY KEY (item_id, depends_on),
                CHECK (item_id <> depends_on)
            )
            """,
            """
            CREATE TABLE comments (
                id {pk},
                item_id {bigint} NOT NULL REFERENCES items (id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('decision', 'progress', 'handoff', 'summary', 'verification')),
                result TEXT CHECK (
                    -- IS NOT NULL is required: a CHECK that evaluates to NULL passes.
                    (kind = 'verification' AND result IS NOT NULL AND result IN ('pass', 'fail', 'blocked'))
                    OR (kind <> 'verification' AND result IS NULL)
                ),
                author TEXT NOT NULL CHECK (length(author) BETWEEN 1 AND 100),
                text TEXT NOT NULL CHECK (length(text) > 0),
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX comments_item ON comments (item_id)",
            """
            CREATE TABLE activity (
                id {pk},
                item_id {bigint} NOT NULL REFERENCES items (id) ON DELETE CASCADE,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                author TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX activity_item ON activity (item_id)",
        ),
    ),
)

CURRENT_VERSION = MIGRATIONS[-1].version

_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def database_version(conn: Any, dialect: Dialect) -> int:
    """The highest applied version, or 0 for a new database. Call inside a transaction."""
    conn.execute(_VERSIONS_TABLE)
    row = conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()
    return row[0] or 0


def upgrade(conn: Any, dialect: Dialect, migrations: tuple[Migration, ...] = MIGRATIONS) -> list[int]:
    """Apply missing migrations in order. Returns the versions applied (empty when current)."""
    latest = migrations[-1].version
    with transaction(conn, dialect):
        current = database_version(conn, dialect)
        if current > latest:
            raise StorageConfigError(
                f"The database schema is at version {current}; this Taskman knows version {latest}. Upgrade Taskman."
            )
        applied = []
        for migration in migrations:
            if migration.version <= current:
                continue
            for statement in migration.statements:
                conn.execute(statement.format(**_TYPES[dialect.name]))
            conn.execute(
                dialect.sql("INSERT INTO schema_versions (version, name, applied_at) VALUES (?, ?, ?)"),
                (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
            )
            applied.append(migration.version)
    return applied
