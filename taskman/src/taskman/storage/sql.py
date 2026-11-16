"""Connections and transactions for the sqlite and postgres backends."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

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
    A database that cannot be opened is a :class:`StorageConfigError` with the driver's reason.
    """
    if settings.backend == "sqlite":
        path = settings.sqlite_path
        if not create and not path.is_file():
            raise StorageConfigError(f"There is no Taskman database at {path}. Run: taskman db upgrade")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # The UI server shares one store across threads; SqlStore serializes access.
            conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        except OSError as exc:
            raise StorageConfigError(f"Cannot open the Taskman database: {exc.strerror}: {exc.filename}") from None
        except sqlite3.DatabaseError as exc:
            raise _cannot_open(exc) from None
        try:
            # A file that is not a database fails here, at the first read of its header.
            conn.execute("PRAGMA schema_version")
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise _cannot_open(exc) from None
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
        try:
            # Keyword values (socket, database, user, password) override the URL's.
            return psycopg.connect(url, autocommit=True, connect_timeout=10, **options), POSTGRES
        except psycopg.Error as exc:
            # A message about a malformed URL can quote the URL's password.
            raise _cannot_open(exc, url, options.get("password"), *_url_secrets(url)) from None
    raise StorageConfigError(f"The {settings.backend} backend has no SQL database.")


def _url_secrets(url: str) -> list[str]:
    """Each text of a Postgres URL that can hold a password, raw and percent-decoded.

    libpq ends the user info at the first ``@`` or ``/``. So a raw ``@`` or ``/``
    in a password puts the rest of the password into the host or database name.
    """
    password = url.partition("://")[2].partition("?")[0].rpartition("@")[0].partition(":")[2]
    texts = [password, *re.split("[@/]", password), *re.findall(r"[?&]password=([^&]*)", url)]
    return [text for raw in texts for text in (raw, unquote(raw))]


def _cannot_open(exc: Exception, *secrets: str | None) -> StorageConfigError:
    """The driver's reason on one line, with each secret replaced by ``***``."""
    reason = " ".join(str(exc).split())
    # The longest first: a part of a password must not hide the rest of a longer one.
    for secret in sorted(filter(None, set(secrets)), key=len, reverse=True):
        reason = reason.replace(secret, "***")
    return StorageConfigError(f"Cannot open the Taskman database: {reason}")


def is_lost(conn: Any, dialect: Dialect) -> bool:
    """Whether a connection is closed or broken, so that no statement can run on it.

    psycopg marks a connection closed when the server ends it, at the first
    statement after that. A SQLite connection is lost only when it was closed.
    """
    if dialect is SQLITE:
        try:
            conn.total_changes
        except sqlite3.ProgrammingError:
            return True
        return False
    return conn.closed


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
