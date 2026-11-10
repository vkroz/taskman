"""Connections and transactions for the sqlite and postgres backends."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from taskman.storage.settings import StorageSettings
from taskman.storage.store import StorageConfigError

# Transaction-scoped advisory lock key that serializes schema upgrades on Postgres.
_UPGRADE_LOCK = 0x746D7367  # "tmsg"


@dataclass(frozen=True, slots=True)
class Dialect:
    """What differs between SQLite and Postgres for Taskman's SQL."""

    name: str

    def sql(self, statement: str) -> str:
        """Statements use ``?`` placeholders; psycopg expects ``%s``."""
        return statement if self.name == "sqlite" else statement.replace("?", "%s")


SQLITE = Dialect("sqlite")
POSTGRES = Dialect("postgres")


def connect(settings: StorageSettings) -> tuple[Any, Dialect]:
    """Open an autocommit connection; callers group statements with :func:`transaction`."""
    if settings.backend == "sqlite":
        path = settings.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
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
def transaction(conn: Any, dialect: Dialect, *, upgrade_lock: bool = False) -> Iterator[None]:
    """Run the block in one transaction that holds the database write lock.

    SQLite takes its write lock at ``BEGIN IMMEDIATE``. On Postgres,
    ``upgrade_lock`` also takes an advisory lock that serializes upgrades.
    """
    conn.execute("BEGIN IMMEDIATE" if dialect is SQLITE else "BEGIN")
    try:
        if upgrade_lock and dialect is POSTGRES:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_UPGRADE_LOCK,))
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
