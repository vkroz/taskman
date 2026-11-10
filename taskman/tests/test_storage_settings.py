"""Storage settings from environment variables and .env."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage.settings import (
    StorageSettings,
    load_env_file,
    parse_env_file,
    repo_root,
    storage_settings,
)
from taskman.storage.store import StorageConfigError

runner = CliRunner()
SECRET = "s3cr3t-P@ss"


def _error(environ: dict[str, str]) -> str:
    with pytest.raises(StorageConfigError) as caught:
        storage_settings(environ)
    return str(caught.value)


# ---- .env parsing and loading ----------------------------------------


def test_parse_env_file_handles_comments_export_and_quotes():
    text = "\n".join(
        [
            "# comment",
            "",
            "export TASKMAN_BACKEND=postgres",
            "TASKMAN_DB_NAME = taskman  # trailing comment",
            "TASKMAN_DB_USER='app user'",
            'TASKMAN_DB_SOCKET="/cloudsql/p:r:i"',
            "not a setting",
        ]
    )
    assert parse_env_file(text) == {
        "TASKMAN_BACKEND": "postgres",
        "TASKMAN_DB_NAME": "taskman",
        "TASKMAN_DB_USER": "app user",
        "TASKMAN_DB_SOCKET": "/cloudsql/p:r:i",
    }


def test_env_file_is_read_from_the_repository_root(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("TASKMAN_BACKEND=sqlite\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    environ: dict[str, str] = {}
    assert repo_root(nested) == tmp_path.resolve()
    assert load_env_file(nested, environ) == tmp_path.resolve() / ".env"
    assert environ == {"TASKMAN_BACKEND": "sqlite"}


def test_environment_wins_over_env_file(tmp_path):
    (tmp_path / ".env").write_text("TASKMAN_BACKEND=sqlite\nTASKMAN_DB_NAME=fromfile\n")
    environ = {"TASKMAN_BACKEND": "postgres"}
    load_env_file(tmp_path, environ)
    assert environ == {"TASKMAN_BACKEND": "postgres", "TASKMAN_DB_NAME": "fromfile"}


def test_missing_env_file_changes_nothing(tmp_path):
    environ = {"KEEP": "1"}
    assert load_env_file(tmp_path, environ) is None
    assert environ == {"KEEP": "1"}


def test_env_file_tracked_by_git_is_ignored(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".env").write_text("TASKMAN_DATABASE_URL=postgresql://attacker.example/db\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".env"], check=True)
    environ: dict[str, str] = {}
    assert load_env_file(tmp_path, environ) is None
    assert environ == {}
    assert "git tracks it" in capsys.readouterr().err


# ---- backend and SQLite ------------------------------------------------


def test_backend_defaults_to_file():
    assert storage_settings({}) == StorageSettings("file")


def test_unknown_backend_names_the_supported_ones():
    message = _error({"TASKMAN_BACKEND": "mysql"})
    assert "Unknown storage backend 'mysql'" in message
    assert "file, sqlite, postgres" in message


def test_sqlite_without_url_uses_the_shared_user_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = storage_settings({"TASKMAN_BACKEND": "sqlite"})
    assert settings.sqlite_path == tmp_path / ".local" / "share" / "taskman" / "taskman.db"


def test_sqlite_url_gives_an_absolute_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert storage_settings({"TASKMAN_BACKEND": "sqlite", "TASKMAN_DATABASE_URL": "sqlite:///var/db/t.db"}).sqlite_path == Path("/var/db/t.db")
    assert storage_settings({"TASKMAN_BACKEND": "sqlite", "TASKMAN_DATABASE_URL": "sqlite://~/t.db"}).sqlite_path == tmp_path / "t.db"


def test_sqlite_rejects_relative_paths_and_other_schemes():
    assert "absolute path" in _error({"TASKMAN_BACKEND": "sqlite", "TASKMAN_DATABASE_URL": "sqlite://data/t.db"})
    assert "must start with sqlite://" in _error({"TASKMAN_BACKEND": "sqlite", "TASKMAN_DATABASE_URL": "postgresql://h/db"})


# ---- Postgres ------------------------------------------------------------


def test_postgres_url():
    settings = storage_settings({"TASKMAN_BACKEND": "postgres", "TASKMAN_DATABASE_URL": "postgresql://app@db:5432/taskman"})
    assert settings.postgres == {"url": "postgresql://app@db:5432/taskman"}


def test_postgres_cloud_sql_socket_with_password_from_environment():
    settings = storage_settings(
        {
            "TASKMAN_BACKEND": "postgres",
            "TASKMAN_DB_SOCKET": "/cloudsql/project:region:instance",
            "TASKMAN_DB_NAME": "taskman",
            "TASKMAN_DB_USER": "taskman_app",
            "TASKMAN_DB_PASSWORD": f" {SECRET}\n",
        }
    )
    assert settings.postgres == {
        "host": "/cloudsql/project:region:instance",
        "dbname": "taskman",
        "user": "taskman_app",
        "password": SECRET,
    }


def test_postgres_password_from_secret_file_drops_surrounding_whitespace(tmp_path):
    secret = tmp_path / "password"
    secret.write_text(f"{SECRET}\n")
    settings = storage_settings(
        {"TASKMAN_BACKEND": "postgres", "TASKMAN_DATABASE_URL": "postgresql://app@db/taskman", "TASKMAN_DB_PASSWORD_FILE": str(secret)}
    )
    assert settings.postgres["password"] == SECRET


def test_postgres_names_every_missing_socket_setting():
    message = _error({"TASKMAN_BACKEND": "postgres", "TASKMAN_DB_NAME": "taskman"})
    assert "Missing: TASKMAN_DB_SOCKET, TASKMAN_DB_USER." in message
    message = _error({"TASKMAN_BACKEND": "postgres"})
    assert "Missing: TASKMAN_DB_SOCKET, TASKMAN_DB_NAME, TASKMAN_DB_USER." in message


def test_postgres_password_sources_are_exclusive_and_checked(tmp_path):
    base = {"TASKMAN_BACKEND": "postgres", "TASKMAN_DATABASE_URL": "postgresql://app@db/taskman"}
    assert "not both" in _error({**base, "TASKMAN_DB_PASSWORD": SECRET, "TASKMAN_DB_PASSWORD_FILE": str(tmp_path / "p")})
    assert "Cannot read the file named by TASKMAN_DB_PASSWORD_FILE" in _error({**base, "TASKMAN_DB_PASSWORD_FILE": str(tmp_path / "missing")})


def test_no_error_or_repr_contains_a_secret_or_url(tmp_path):
    secret_file = tmp_path / "password"
    secret_file.write_text(SECRET)
    cases = [
        {"TASKMAN_BACKEND": "postgres", "TASKMAN_DATABASE_URL": f"mysql://app:{SECRET}@db/x"},
        {"TASKMAN_BACKEND": "sqlite", "TASKMAN_DATABASE_URL": f"postgresql://app:{SECRET}@db/x"},
        {"TASKMAN_BACKEND": "postgres", "TASKMAN_DB_PASSWORD": SECRET},
        {"TASKMAN_BACKEND": "postgres", "TASKMAN_DATABASE_URL": "postgresql://db/x", "TASKMAN_DB_PASSWORD": SECRET, "TASKMAN_DB_PASSWORD_FILE": str(secret_file)},
    ]
    for environ in cases:
        assert SECRET not in _error(environ)
    settings = storage_settings({"TASKMAN_BACKEND": "postgres", "TASKMAN_DATABASE_URL": f"postgresql://app:{SECRET}@db/x"})
    assert SECRET not in repr(settings)


# ---- CLI -------------------------------------------------------------------


def test_cli_reports_sql_backends_as_not_available_yet(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKMAN_BACKEND", "sqlite")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 2
    assert "The sqlite backend is not available yet" in result.output


def test_cli_reads_settings_from_env_file(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("TASKMAN_BACKEND=nosuch\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TASKMAN_BACKEND", raising=False)
    result = runner.invoke(app, ["list"])
    os.environ.pop("TASKMAN_BACKEND", None)  # the CLI loaded it into this process
    assert result.exit_code == 2
    assert "Unknown storage backend 'nosuch'" in result.output


def test_help_works_with_wrong_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKMAN_BACKEND", "nosuch")
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "TASKMAN_BACKEND" in result.output
