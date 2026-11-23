"""Versioned SQL schema for the work-item model in ``docs/data-model.md``.

Each migration has a version number. ``upgrade`` applies the migrations
that the database has not recorded, in order, in one transaction, and
records each version in ``schema_versions``. A version can be missing below
the highest one when branches merge in another order than their numbers.
"""
from __future__ import annotations

from collections.abc import Callable
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
    # A step that differs between the dialects; it runs after ``statements``.
    run: Callable[[Any, Dialect], None] | None = None


# Column types that differ between the dialects.
_TYPES = {
    "sqlite": {"pk": "INTEGER PRIMARY KEY AUTOINCREMENT", "bigint": "INTEGER", "real": "REAL"},
    "postgres": {"pk": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY", "bigint": "BIGINT", "real": "DOUBLE PRECISION"},
}

_OLD_TYPE_CHECK = "CHECK (type IN ('epic', 'feat', 'task'))"
_NEW_TYPE_CHECK = "CHECK (type IN ('epic', 'feat', 'task', 'bugfix'))"


def _widen_checks(conn: Any, dialect: Dialect, table: str, checks: dict[str, tuple[str, str]], purpose: str) -> None:
    """Replace CHECK constraints of ``table`` with wider ones. Rows, ids, keys and indexes stay as they are.

    ``checks`` maps each Postgres constraint name to its old and new CHECK text.
    Postgres replaces the named constraints. SQLite cannot alter a CHECK and its
    usual table rebuild is unsafe here: with foreign keys on, which cannot change
    inside a transaction, ``DROP TABLE items`` deletes the comments, labels and
    activity of every item. So SQLite edits the stored table definition, the
    procedure that https://sqlite.org/lang_altertable.html gives to change CHECK
    constraints. All existing rows satisfy the wider constraints.
    """
    if dialect.name == "postgres":
        changes = ", ".join(f"DROP CONSTRAINT {name}, ADD CONSTRAINT {name} {new}" for name, (_, new) in checks.items())
        conn.execute(f"ALTER TABLE {table} {changes}")
        return
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    try:
        for old, new in checks.values():
            conn.execute(
                "UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE type = 'table' AND name = ?",
                (old, new, table),
            )
        # A new schema version makes every connection read the new definition.
        conn.execute(f"PRAGMA schema_version = {int(version) + 1}")
    finally:
        conn.execute("PRAGMA writable_schema = OFF")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
    if any(new not in sql for _, new in checks.values()):
        raise StorageConfigError(f"The {table} table has an unexpected definition; cannot {purpose}.")
    problems = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
    if problems != ["ok"]:
        raise StorageConfigError(f"The database failed its integrity check: {'; '.join(problems)}")


def _allow_bugfix_type(conn: Any, dialect: Dialect) -> None:
    """Let ``items.type`` hold ``bugfix``."""
    _widen_checks(conn, dialect, "items", {"items_type_check": (_OLD_TYPE_CHECK, _NEW_TYPE_CHECK)},
                  "add the bugfix type")


_OLD_KIND_CHECK = "CHECK (kind IN ('decision', 'progress', 'handoff', 'summary', 'verification'))"
_NEW_KIND_CHECK = "CHECK (kind IN ('decision', 'progress', 'handoff', 'summary', 'verification', 'review'))"
_OLD_RESULT_CHECK = """CHECK (
                    -- IS NOT NULL is required: a CHECK that evaluates to NULL passes.
                    (kind = 'verification' AND result IS NOT NULL AND result IN ('pass', 'fail', 'blocked'))
                    OR (kind <> 'verification' AND result IS NULL)
                )"""
_NEW_RESULT_CHECK = """CHECK (
                    -- IS NOT NULL is required: a CHECK that evaluates to NULL passes.
                    (kind = 'verification' AND result IS NOT NULL AND result IN ('pass', 'fail', 'blocked'))
                    OR (kind = 'review' AND result IS NOT NULL AND result IN ('pass', 'fail'))
                    OR (kind NOT IN ('verification', 'review') AND result IS NULL)
                )"""


def _allow_review_comments(conn: Any, dialect: Dialect) -> None:
    """Let a comment have the kind ``review`` with the result ``pass`` or ``fail``."""
    _widen_checks(conn, dialect, "comments", {
        "comments_kind_check": (_OLD_KIND_CHECK, _NEW_KIND_CHECK),
        # Postgres names a CHECK on two columns after the table only.
        "comments_check": (_OLD_RESULT_CHECK, _NEW_RESULT_CHECK),
    }, "add the review comment kind")


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
    Migration(
        2,
        "persons and access tokens",
        (
            """
            CREATE TABLE persons (
                id {pk},
                name TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 60),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE tokens (
                id {pk},
                person_id {bigint} NOT NULL REFERENCES persons (id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                revoked_at TEXT
            )
            """,
        ),
    ),
    Migration(
        3,
        "browser logins",
        (
            """
            CREATE TABLE logins (
                id {pk},
                state_hash TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL CHECK (kind IN ('cli', 'token')),
                cli_port INTEGER,
                cli_state TEXT,
                code_challenge TEXT,
                label TEXT NOT NULL DEFAULT '',
                nonce TEXT NOT NULL,
                verifier TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                person TEXT,
                exchange_hash TEXT UNIQUE,
                exchanged_at TEXT
            )
            """,
        ),
    ),
    Migration(4, "bugfix item type", (), run=_allow_bugfix_type),
    Migration(
        5,
        "index of login start times",
        ("CREATE INDEX logins_created_at ON logins (created_at)",),
    ),
    Migration(
        6,
        "repositories of items",
        (
            """
            CREATE TABLE repositories (
                item_id {bigint} NOT NULL REFERENCES items (id) ON DELETE CASCADE,
                address TEXT NOT NULL CHECK (length(address) BETWEEN 1 AND 200),
                PRIMARY KEY (item_id, address)
            )
            """,
        ),
    ),
    Migration(7, "review comments", (), run=_allow_review_comments),
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
        recorded = {row[0] for row in conn.execute("SELECT version FROM schema_versions").fetchall()}
        applied = []
        for migration in migrations:
            if migration.version in recorded:
                continue
            for statement in migration.statements:
                conn.execute(statement.format(**_TYPES[dialect.name]))
            if migration.run:
                migration.run(conn, dialect)
            conn.execute(
                dialect.sql("INSERT INTO schema_versions (version, name, applied_at) VALUES (?, ?, ?)"),
                (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
            )
            applied.append(migration.version)
    return applied
