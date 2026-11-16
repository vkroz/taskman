"""Versioned SQL schema and the db upgrade command."""
from __future__ import annotations

import multiprocessing

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage.schema import CURRENT_VERSION, MIGRATIONS, Migration, database_version, upgrade
from taskman.storage.settings import StorageSettings
from taskman.storage.sql import connect, transaction
from taskman.storage.store import StorageConfigError

runner = CliRunner()
TABLES = {"projects", "items", "labels", "dependencies", "comments", "activity", "persons", "tokens", "logins",
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


def test_a_version_below_the_highest_is_applied_when_missing(db, sql_settings, monkeypatch):
    # As when a branch with a higher version merges and deploys before a branch with a lower one.
    import taskman.storage.sql_store as sql_store

    conn, dialect = db
    earlier = Migration(CURRENT_VERSION + 1, "earlier branch", ("CREATE TABLE earlier_probe (id INTEGER)",))
    later = Migration(CURRENT_VERSION + 2, "later branch", ("CREATE TABLE later_probe (id INTEGER)",))
    assert upgrade(conn, dialect, MIGRATIONS + (later,))[-1] == later.version
    merged = MIGRATIONS + (earlier, later)
    monkeypatch.setattr(sql_store, "MIGRATIONS", merged)
    monkeypatch.setattr(sql_store, "CURRENT_VERSION", later.version)
    with pytest.raises(StorageConfigError, match=f"lacks version {earlier.version}. Run: taskman db upgrade"):
        sql_store.open_sql_store(sql_settings)
    assert upgrade(conn, dialect, merged) == [earlier.version]
    assert "earlier_probe" in _tables(conn, dialect)
    assert upgrade(conn, dialect, merged) == []
    sql_store.open_sql_store(sql_settings).close()


def _table_indexes(conn, dialect, table: str) -> set[str]:
    if dialect.name == "sqlite":
        rows = conn.execute(dialect.sql("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?"), (table,))
    else:
        rows = conn.execute(dialect.sql("SELECT indexname FROM pg_indexes WHERE tablename = ?"), (table,))
    return {row[0] for row in rows.fetchall()}


def test_a_database_at_version_3_gets_the_index_of_login_start_times(db):
    conn, dialect = db
    upgrade(conn, dialect, tuple(m for m in MIGRATIONS if m.version <= 3))
    with transaction(conn, dialect):
        conn.execute(
            dialect.sql("INSERT INTO logins (state_hash, kind, nonce, verifier, created_at) VALUES (?, ?, ?, ?, ?)"),
            ("kept", "token", "n", "v", "2026-11-16T12:00:00+00:00"),
        )
    assert "logins_created_at" not in _table_indexes(conn, dialect, "logins")
    assert upgrade(conn, dialect) == [m.version for m in MIGRATIONS if m.version > 3]
    assert "logins_created_at" in _table_indexes(conn, dialect, "logins")
    assert conn.execute("SELECT state_hash FROM logins").fetchall() == [("kept",)]


def test_the_login_cleanup_uses_the_index_of_login_start_times(tmp_path):
    # SQLite only: Postgres scans a small table in place of its index.
    conn, dialect = connect(StorageSettings("sqlite", sqlite_path=tmp_path / "plan.db"))
    upgrade(conn, dialect)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN DELETE FROM logins WHERE created_at < ? AND (finished_at IS NULL OR created_at < ?)",
        ("a", "b"),
    ).fetchall()
    conn.close()
    assert any("logins_created_at" in row[-1] for row in plan), plan


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
