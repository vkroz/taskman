"""Connections and transactions for the sqlite and postgres backends."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from taskman.storage.settings import StorageSettings
from taskman.storage.store import StorageConfigError

# Transaction-scoped advisory lock key: the database write lock on Postgres.
_WRITE_LOCK = 0x746D7367  # "tmsg"


@dataclass(frozen=True, slots=True)
class Dialect:
    """What differs between SQLite and Postgres for Taskman's SQL."""

    name: str

    def sql(self, statement: str) -> str:
        """Statements use ``?`` placeholders; psycopg expects ``%s``."""
        return statement if self.name == "sqlite" else statement.replace("?", "%s")


SQLITE = Dialect("sqlite")
POSTGRES = Dialect("postgres")


def connect(settings: StorageSettings, *, create: bool = True) -> tuple[Any, Dialect]:
    """Open an autocommit connection; callers group statements with :func:`transaction`.

    With ``create=False`` a missing SQLite file is an error, not a new empty database.
    """
    if settings.backend == "sqlite":
        path = settings.sqlite_path
        if not create and not path.is_file():
            raise StorageConfigError(f"There is no Taskman database at {path}. Run: taskman db upgrade")
        path.parent.mkdir(parents=True, exist_ok=True)
        # The UI server shares one store across threads; SqlStore serializes access.
        conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn, SQLITE
    if settings.backend == "postgres":
        try:
            import psycopg
        except ImportError:
            raise StorageConfigError(
                "The postgres backend needs psycopg. Install the extra: pip install 'taskman[postgres]'."
            ) from None
        options = dict(settings.postgres)
        url = options.pop("url", "")
        # Keyword values (socket, database, user, password) override the URL's.
        return psycopg.connect(url, autocommit=True, connect_timeout=10, **options), POSTGRES
    raise StorageConfigError(f"The {settings.backend} backend has no SQL database.")


@contextmanager
def transaction(conn: Any, dialect: Dialect) -> Iterator[None]:
    """Run the block in one transaction that holds the database write lock.

    SQLite takes its write lock at ``BEGIN IMMEDIATE``. Postgres takes an
    advisory lock as the first statement of a READ COMMITTED transaction, so
    each later statement sees the rows of writers that committed before it.
    Both wait at most 30 seconds for the lock.
    """
    conn.execute("BEGIN IMMEDIATE" if dialect is SQLITE else "BEGIN ISOLATION LEVEL READ COMMITTED")
    try:
        if dialect is POSTGRES:
            conn.execute("SET LOCAL lock_timeout = '30s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_WRITE_LOCK,))
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
