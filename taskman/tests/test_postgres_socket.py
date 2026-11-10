"""A Postgres connection through a unix socket directory, as Cloud SQL provides on Cloud Run.

On Cloud Run, the Cloud SQL Auth Proxy serves ``/cloudsql/<instance>/.s.PGSQL.5432``
and forwards it to the instance. This test runs the same kind of forwarder in
front of the test database and connects with ``TASKMAN_DB_SOCKET``,
``TASKMAN_DB_NAME``, ``TASKMAN_DB_USER`` and ``TASKMAN_DB_PASSWORD_FILE``.
It is skipped without ``TASKMAN_TEST_DATABASE_URL``.
"""
from __future__ import annotations

import shutil
import socket
import socketserver
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import pytest

from taskman import lifecycle
from taskman.storage.schema import upgrade
from taskman.storage.settings import storage_settings
from taskman.storage.sql import connect
from taskman.storage.sql_store import open_sql_store
from tests.conftest import postgres_test_settings, reset_postgres


class _Forwarder(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Forward each unix-socket connection to a TCP address."""

    daemon_threads = True

    def __init__(self, path: str, target: tuple[str, int]) -> None:
        self.target = target
        self.connections = 0
        super().__init__(path, _Pipe)


class _Pipe(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.server.connections += 1
        upstream = socket.create_connection(self.server.target)

        def pump(source: socket.socket, target: socket.socket) -> None:
            try:
                while data := source.recv(65536):
                    target.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    target.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        back = threading.Thread(target=pump, args=(upstream, self.request), daemon=True)
        back.start()
        pump(self.request, upstream)
        back.join()
        upstream.close()


@pytest.fixture
def forwarder():
    settings = postgres_test_settings()
    reset_postgres(settings)
    url = urlparse(settings.postgres["url"])
    # A socket path has at most 104 bytes on macOS; tmp_path is longer.
    directory = Path(tempfile.mkdtemp(prefix="tm-", dir="/tmp"))
    # libpq names the socket after the port; without a port it uses 5432.
    server = _Forwarder(str(directory / ".s.PGSQL.5432"), (url.hostname, url.port or 5432))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, directory, url
    server.shutdown()
    server.server_close()
    shutil.rmtree(directory)


def test_connects_through_a_unix_socket_directory(forwarder, tmp_path):
    server, directory, url = forwarder
    password_file = tmp_path / "password"
    password_file.write_text(f"{url.password or ''}\n", encoding="utf-8")
    settings = storage_settings(
        {
            "TASKMAN_BACKEND": "postgres",
            "TASKMAN_DB_SOCKET": str(directory),
            "TASKMAN_DB_NAME": url.path.lstrip("/"),
            "TASKMAN_DB_USER": url.username,
            "TASKMAN_DB_PASSWORD_FILE": str(password_file),
        }
    )
    conn, dialect = connect(settings)
    try:
        assert conn.info.host == str(directory)
        upgrade(conn, dialect)
    finally:
        conn.close()
    store = open_sql_store(settings, actor="pytest")
    try:
        assert lifecycle.create(store, "Over the socket", "task", None, project="TM").id == "TM-1"
        assert store.get("TM-1").title == "Over the socket"
    finally:
        store.conn.close()
    assert server.connections == 2
