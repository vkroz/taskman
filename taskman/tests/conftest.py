"""Session-scoped Postgres for the server tests.

The suite uses a real Postgres server. Set TASKMAN_TEST_DATABASE_URL to use an
existing one. Otherwise the tests start a Docker container named
``taskman-pytest-pg`` on port 54329. That is the same engine the Compose stack
runs; it is not an in-memory substitute.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time

import pytest

from taskman.db import connect, migrate

TOKEN = "test-token-value"
_PORT = 54329
_NAME = "taskman-pytest-pg"


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


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def _ensure_container() -> str:
    override = os.environ.get("TASKMAN_TEST_DATABASE_URL")
    if override:
        return override
    if _docker("inspect", _NAME).returncode != 0:
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
            "POSTGRES_DB=taskman",
            "-p",
            f"{_PORT}:5432",
            "postgres:16-alpine",
        )
        if started.returncode != 0:
            pytest.skip(f"Postgres is not available and docker run failed: {started.stderr.strip()}")
    elif not _accepting(_PORT):
        _docker("start", _NAME)
    return f"postgresql://taskman:taskman@127.0.0.1:{_PORT}/taskman"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _ensure_container()
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
    pytest.skip(f"Postgres did not become ready: {last}")


@pytest.fixture
def database(database_url, monkeypatch):
    monkeypatch.setenv("TASKMAN_DATABASE_URL", database_url)
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", TOKEN)
    migrate(database_url)
    with connect(database_url) as conn:
        conn.execute("TRUNCATE TABLE events, task_dependencies, tasks RESTART IDENTITY CASCADE")
    return database_url
