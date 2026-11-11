"""Versioned SQL schema and the db upgrade command."""
from __future__ import annotations

import multiprocessing

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage.schema import CURRENT_VERSION, MIGRATIONS, Migration, database_version, upgrade
from taskman.storage.sql import connect, transaction
from taskman.storage.store import StorageConfigError

runner = CliRunner()
TABLES = {"projects", "items", "labels", "dependencies", "comments", "activity", "persons", "tokens",
          "schema_versions"}


def _tables(conn, dialect) -> set[str]:
    if dialect.name == "sqlite":
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    else:
        rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    return {row[0] for row in rows.fetchall()}


def _schema_snapshot(conn, dialect) -> list[tuple]:
    if dialect.name == "sqlite":
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
    else:
        rows = conn.execute(
            "SELECT table_name, column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
        )
    return [tuple(row) for row in rows.fetchall()] + [
        tuple(row) for row in conn.execute("SELECT version, name, applied_at FROM schema_versions").fetchall()
    ]


@pytest.fixture
def db(sql_settings):
    conn, dialect = connect(sql_settings)
    yield conn, dialect
    conn.close()


def test_upgrade_creates_the_current_schema_in_an_empty_database(db):
    conn, dialect = db
    assert upgrade(conn, dialect) == [migration.version for migration in MIGRATIONS]
    assert _tables(conn, dialect) == TABLES
    with transaction(conn, dialect):
        assert database_version(conn, dialect) == CURRENT_VERSION


def test_upgrade_on_a_current_database_changes_nothing(db):
    conn, dialect = db
    upgrade(conn, dialect)
    before = _schema_snapshot(conn, dialect)
    assert upgrade(conn, dialect) == []
    assert _schema_snapshot(conn, dialect) == before


def test_older_database_gets_only_the_missing_versions(db):
    conn, dialect = db
    upgrade(conn, dialect, MIGRATIONS)
    newer = MIGRATIONS + (Migration(CURRENT_VERSION + 1, "test addition", ("CREATE TABLE upgrade_probe (id INTEGER)",)),)
    assert upgrade(conn, dialect, newer) == [CURRENT_VERSION + 1]
    assert "upgrade_probe" in _tables(conn, dialect)
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()]
    assert versions == [m.version for m in newer]


def test_database_newer_than_taskman_is_refused(db):
    conn, dialect = db
    newer = MIGRATIONS + (Migration(CURRENT_VERSION + 1, "future", ("CREATE TABLE future_probe (id INTEGER)",)),)
    upgrade(conn, dialect, newer)
    with pytest.raises(StorageConfigError, match="Upgrade Taskman"):
        upgrade(conn, dialect)


def test_failed_migration_leaves_the_database_unchanged(db):
    conn, dialect = db
    upgrade(conn, dialect)
    before = _schema_snapshot(conn, dialect)
    broken = MIGRATIONS + (Migration(CURRENT_VERSION + 1, "broken", ("CREATE TABLE broken_probe (id INTEGER)", "NOT VALID SQL")),)
    with pytest.raises(Exception):
        upgrade(conn, dialect, broken)
    assert "broken_probe" not in _tables(conn, dialect)
    assert _schema_snapshot(conn, dialect) == before


def test_schema_enforces_model_rules(db):
    conn, dialect = db
    upgrade(conn, dialect)
    q = dialect.sql
    now = "2026-11-09T00:00:00+00:00"
    with transaction(conn, dialect):
        conn.execute(q("INSERT INTO projects (key, name, created_at) VALUES (?, ?, ?)"), ("TM", "TM", now))
        conn.execute(
            q(
                "INSERT INTO items (project, number, type, title, status, created_at, created_by, changed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            ("TM", 1, "task", "First", "backlog", now, "pytest", now),
        )
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    rejected = [
        # A second item with the same key number.
        ("INSERT INTO items (project, number, type, title, status, created_at, created_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
         ("TM", 1, "task", "Again", "backlog", now, "pytest", now)),
        # A verification comment without a result.
        ("INSERT INTO comments (item_id, kind, author, text, revision, created_at) VALUES (?, ?, ?, ?, ?, ?)",
         (item_id, "verification", "pytest", "ran checks", 0, now)),
        # A result on a comment that is not a verification.
        ("INSERT INTO comments (item_id, kind, result, author, text, revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
         (item_id, "decision", "pass", "pytest", "chose", 0, now)),
        # An unknown status.
        ("INSERT INTO items (project, number, type, title, status, created_at, created_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
         ("TM", 2, "task", "Bad", "review", now, "pytest", now)),
        # An item that depends on itself.
        ("INSERT INTO dependencies (item_id, depends_on) VALUES (?, ?)", (item_id, item_id)),
    ]
    for statement, values in rejected:
        with pytest.raises(Exception):
            with transaction(conn, dialect):
                conn.execute(q(statement), values)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def _upgrade_in_process(settings, results):
    conn, dialect = connect(settings)
    try:
        results.put(upgrade(conn, dialect))
    finally:
        conn.close()


def test_concurrent_upgrades_apply_each_version_once(sql_settings):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [context.Process(target=_upgrade_in_process, args=(sql_settings, results)) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
        assert worker.exitcode == 0
    applied = sorted((results.get(timeout=5) for _ in workers), key=len)
    assert applied == [[], [], [migration.version for migration in MIGRATIONS]]
    conn, _ = connect(sql_settings)
    try:
        assert conn.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0] == len(MIGRATIONS)
    finally:
        conn.close()


def test_db_upgrade_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKMAN_BACKEND", "sqlite")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{tmp_path / 'cli.db'}")
    first = runner.invoke(app, ["db", "upgrade"])
    assert first.exit_code == 0, first.output
    versions = ", ".join(str(migration.version) for migration in MIGRATIONS)
    assert f"Applied schema versions: {versions}." in first.output
    second = runner.invoke(app, ["db", "upgrade"])
    assert second.exit_code == 0
    assert "The database schema is current." in second.output


def test_db_upgrade_refuses_the_file_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKMAN_BACKEND", "file")
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 2
    assert "The file backend was removed" in result.output
