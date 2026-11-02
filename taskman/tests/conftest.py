"""Session-scoped Postgres for the server tests.

The suite uses a real Postgres server. Set TASKMAN_TEST_DATABASE_URL to use an
existing one. Otherwise the tests start a Docker container named
``taskman-pytest-pg-test`` on port 54329. That is the same engine the Compose
stack runs; it is not an in-memory substitute.

The tests TRUNCATE every Taskman table. They refuse to run unless the database
name ends in ``_test``. When TASKMAN_TEST_DATABASE_URL or CI is set, an
unreachable database fails the run instead of skipping it.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time

import pytest
from psycopg.conninfo import conninfo_to_dict

from taskman.db import connect, migrate

TOKEN = "test-token-value"
_PORT = 54329
_NAME = "taskman-pytest-pg-test"
_DB = "taskman_test"


def _strict() -> bool:
    """True when a missing database must fail the run instead of skipping it."""
    return bool(os.environ.get("TASKMAN_TEST_DATABASE_URL") or os.environ.get("CI"))


def _unavailable(reason: str) -> None:
    if _strict():
        pytest.fail(f"{reason} (TASKMAN_TEST_DATABASE_URL or CI is set, so this is a failure)", pytrace=False)
    pytest.skip(reason)


def database_name_error(url: str) -> str | None:
    """Reason to refuse ``url``, or None when its database name ends in ``_test``."""
    try:
        name = conninfo_to_dict(url).get("dbname") or ""
    except Exception as exc:  # noqa: BLE001 — any parse error is a refusal
        return f"cannot parse the test database URL: {exc}"
    if not name.endswith("_test"):
        return (
            f"refusing to run: the tests TRUNCATE every Taskman table and database {name!r} "
            "does not end in '_test'. Point TASKMAN_TEST_DATABASE_URL at a disposable database."
        )
    return None


def _accepting(port: int) -> bool:
    probe = socket.socket()
    probe.settimeout(0.3)
    try:
        probe.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _docker(*args: str) -> subprocess.CompletedProcess | None:
    """Run docker, or return None when the docker binary is not installed."""
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None


def _ensure_container() -> str:
    override = os.environ.get("TASKMAN_TEST_DATABASE_URL")
    if override:
        return override
    inspected = _docker("inspect", _NAME)
    if inspected is None:
        _unavailable("Postgres is not available: docker is not installed and TASKMAN_TEST_DATABASE_URL is unset")
    if inspected.returncode != 0:
        started = _docker(
            "run",
            "-d",
            "--name",
            _NAME,
            "-e",
            "POSTGRES_USER=taskman",
            "-e",
            "POSTGRES_PASSWORD=taskman",
            "-e",
            f"POSTGRES_DB={_DB}",
            "-p",
            f"{_PORT}:5432",
            "postgres:16-alpine",
        )
        if started.returncode != 0:
            _unavailable(f"Postgres is not available and docker run failed: {started.stderr.strip()}")
    elif not _accepting(_PORT):
        _docker("start", _NAME)
    return f"postgresql://taskman:taskman@127.0.0.1:{_PORT}/{_DB}"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _ensure_container()
    refusal = database_name_error(url)
    if refusal:
        pytest.exit(refusal, returncode=2)
    deadline = time.time() + 60
    last = ""
    while time.time() < deadline:
        try:
            with connect(url) as conn:
                conn.execute("SELECT 1")
            return url
        except Exception as exc:  # noqa: BLE001 — wait for the container to accept logins
            last = str(exc)
            time.sleep(0.5)
    _unavailable(f"Postgres did not become ready: {last}")


@pytest.fixture
def database(database_url, monkeypatch):
    monkeypatch.setenv("TASKMAN_DATABASE_URL", database_url)
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", TOKEN)
    migrate(database_url)
    with connect(database_url) as conn:
        conn.execute("TRUNCATE TABLE events, task_dependencies, tasks RESTART IDENTITY CASCADE")
    return database_url
