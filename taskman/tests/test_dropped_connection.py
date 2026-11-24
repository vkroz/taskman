"""A Postgres connection that drops with no FIN or RST (TASKMAN-36).

A proxy in front of the test database stops forwarding bytes of the open
connections and does not close them, as a lost network path does. The store
must notice within ``BEGIN_TIMEOUT`` and continue on a new connection. The test
runs over TCP and over a Unix socket, as Cloud SQL provides on Cloud Run.
It is skipped without ``TASKMAN_TEST_DATABASE_URL``.
"""
from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from taskman.storage import sql, sql_store
from taskman.storage.schema import upgrade
from taskman.storage.settings import StorageSettings
from taskman.storage.sql import connect
from taskman.storage.sql_store import open_sql_store
from tests.conftest import postgres_test_settings, reset_postgres

# On the code before TASKMAN-36 a read waits for this, not for minutes, so the test fails and does not hang.
RELEASE_AFTER = 20.0


class _Blackhole:
    """Forward connections to the database until :meth:`drop`; then hold their bytes and keep them open."""

    def __init__(self, listener: socket.socket, target: tuple[str, int]) -> None:
        self.listener = listener
        self.target = target
        self.dropped: list[socket.socket] = []
        self.refuse = False  # end each new connection at once, as a database that is away does
        self._open: list[tuple[socket.socket, socket.socket]] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            if self.refuse:
                client.close()
                continue
            upstream = socket.create_connection(self.target)
            pair = (client, upstream)
            with self._lock:
                self._open.append(pair)
            for source, target in (pair, pair[::-1]):
                threading.Thread(target=self._pump, args=(source, target, pair), daemon=True).start()

    def _pump(self, source: socket.socket, target: socket.socket, pair) -> None:
        try:
            while data := source.recv(65536):
                deadline = time.monotonic() + RELEASE_AFTER
                while self._is_dropped(pair):
                    if time.monotonic() > deadline:
                        source.close()
                        target.close()
                        return
                    time.sleep(0.05)
                target.sendall(data)
        except OSError:
            pass

    def _is_dropped(self, pair) -> bool:
        with self._lock:
            return pair[0] in self.dropped

    def drop(self) -> None:
        """The open connections get no more bytes; new connections work."""
        with self._lock:
            self.dropped.extend(client for client, _ in self._open)

    def close(self) -> None:
        try:
            self.listener.shutdown(socket.SHUT_RDWR)  # on Linux, close alone does not wake accept()
        except OSError:
            pass
        self.listener.close()
        with self._lock:
            for pair in self._open:
                for sock in pair:
                    sock.close()


@pytest.fixture(params=["tcp", "unix"])
def blackhole(request):
    settings = postgres_test_settings()
    reset_postgres(settings)
    conn, dialect = connect(settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    url = urlparse(settings.postgres["url"])
    target = (url.hostname, url.port or 5432)
    directory = None
    if request.param == "tcp":
        listener = socket.create_server(("127.0.0.1", 0))
        options = {"host": "127.0.0.1", "port": str(listener.getsockname()[1])}
    else:
        # A socket path has at most 104 bytes on macOS; tmp_path is longer.
        directory = Path(tempfile.mkdtemp(prefix="tm-", dir="/tmp"))
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(directory / ".s.PGSQL.5432"))
        listener.listen()
        options = {"host": str(directory), "port": "5432"}
    proxy = _Blackhole(listener, target)
    yield proxy, StorageSettings("postgres", postgres={**settings.postgres, **options})
    proxy.close()
    if directory:
        shutil.rmtree(directory)


def test_a_read_after_a_silent_drop_continues_on_a_new_connection(blackhole, monkeypatch):
    proxy, settings = blackhole
    monkeypatch.setattr(sql, "BEGIN_TIMEOUT", 1.0)
    store = open_sql_store(settings, actor="tester")
    try:
        assert store.projects() == []
        first = store.conn
        proxy.drop()
        started = time.monotonic()
        assert store.projects() == []
        elapsed = time.monotonic() - started
        assert store.conn is not first and first.closed
        # The time limit, at most one reconnect, and the first backoff delay.
        assert elapsed < 1.0 + sql_store.RETRY_DELAYS[0] + 3, elapsed
    finally:
        store.close()


def test_a_write_after_a_silent_drop_is_made_once(blackhole, monkeypatch):
    proxy, settings = blackhole
    monkeypatch.setattr(sql, "BEGIN_TIMEOUT", 1.0)
    store = open_sql_store(settings, actor="tester")
    try:
        store.projects()
        proxy.drop()
        store.create(item_type="task", title="After the drop", body="", parent_id=None, status="backlog",
                     draft=True, project="TM")
        assert [item.title for item in store.items()] == ["After the drop"]
    finally:
        store.close()


def test_the_start_is_tried_again_with_backoff_while_the_database_is_away(blackhole, monkeypatch):
    proxy, settings = blackhole
    monkeypatch.setattr(sql, "BEGIN_TIMEOUT", 1.0)
    waits = []
    monkeypatch.setattr(sql_store, "time", SimpleNamespace(sleep=waits.append))  # the proxy keeps its own sleep
    store = open_sql_store(settings, actor="tester")
    try:
        store.projects()
        proxy.drop()
        proxy.refuse = True  # no new connection opens
        with pytest.raises(Exception):
            store.projects()
        assert waits == list(sql_store.RETRY_DELAYS)
    finally:
        store.close()


def test_an_answer_before_the_time_limit_keeps_the_connection(blackhole, monkeypatch):
    _, settings = blackhole
    monkeypatch.setattr(sql, "BEGIN_TIMEOUT", 0.2)
    store = open_sql_store(settings, actor="tester")
    try:
        first = store.conn
        for _ in range(20):
            store.projects()
        time.sleep(0.5)  # a timer that was not stopped fires in this time
        assert store.conn is first and not first.closed
        assert store.projects() == [] and store.conn is first
    finally:
        store.close()
