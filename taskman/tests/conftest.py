"""Shared fixtures for SQL storage tests.

SQLite tests always run. Postgres tests run when ``TASKMAN_TEST_DATABASE_URL``
points at a database whose name ends in ``_test``; they drop and recreate its
``public`` schema, so a non-test database is refused. Without the variable,
Postgres tests are reported as skipped.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

from taskman.storage.settings import StorageSettings

TEST_DATABASE_URL = os.environ.get("TASKMAN_TEST_DATABASE_URL", "").strip()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """No test reaches the user's default database under ``~/.local/share/taskman``."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch) -> StorageSettings:
    """An upgraded SQLite database in ``tmp_path``, selected through the environment.

    Use with the helpers in ``tests/sql_helpers.py``.
    """
    from taskman.storage.schema import upgrade
    from taskman.storage.sql import connect

    for key in list(os.environ):
        if key.startswith("TASKMAN_") and key != "TASKMAN_TEST_DATABASE_URL":
            monkeypatch.delenv(key)
    path = tmp_path / "taskman.db"
    monkeypatch.setenv("TASKMAN_BACKEND", "sqlite")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{path}")
    settings = StorageSettings("sqlite", sqlite_path=path)
    conn, dialect = connect(settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    return settings


def postgres_test_settings() -> StorageSettings:
    if not TEST_DATABASE_URL:
        pytest.skip("Postgres tests need TASKMAN_TEST_DATABASE_URL")
    name = urlparse(TEST_DATABASE_URL).path.lstrip("/")
    if not name.endswith("_test"):
        pytest.fail(f"TASKMAN_TEST_DATABASE_URL must name a database ending in _test, not {name!r}")
    return StorageSettings("postgres", postgres={"url": TEST_DATABASE_URL})


def reset_postgres(settings: StorageSettings) -> None:
    import psycopg

    with psycopg.connect(settings.postgres["url"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(params=["sqlite", "postgres"])
def sql_settings(request, tmp_path) -> StorageSettings:
    """Settings for an empty database of each SQL dialect."""
    if request.param == "sqlite":
        return StorageSettings("sqlite", sqlite_path=tmp_path / "taskman.db")
    settings = postgres_test_settings()
    reset_postgres(settings)
    return settings
