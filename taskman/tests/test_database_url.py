"""Connection settings: a URL, or a Cloud SQL socket plus name, user, and password."""
from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict
from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.serve import ServeConfigError, serve_config
from taskman.db import database_url

runner = CliRunner()

_DB_ENV = (
    "TASKMAN_DATABASE_URL",
    "TASKMAN_DB_SOCKET",
    "TASKMAN_DB_NAME",
    "TASKMAN_DB_USER",
    "TASKMAN_DB_PASSWORD",
    "TASKMAN_ACCESS_TOKEN",
    "PORT",
    "TASKMAN_WEB_WORKERS",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _DB_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_database_url_strips_whitespace(clean_env):
    clean_env.setenv("TASKMAN_DATABASE_URL", "  postgresql://taskman:taskman@db:5432/taskman  ")
    assert database_url() == "postgresql://taskman:taskman@db:5432/taskman"


def test_database_url_wins_over_socket_parts(clean_env):
    clean_env.setenv("TASKMAN_DATABASE_URL", "postgresql://taskman:taskman@db:5432/taskman")
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/ignored")
    clean_env.setenv("TASKMAN_DB_NAME", "other")
    clean_env.setenv("TASKMAN_DB_USER", "other")
    clean_env.setenv("TASKMAN_DB_PASSWORD", "other")
    assert database_url() == "postgresql://taskman:taskman@db:5432/taskman"


def test_blank_database_url_falls_through_to_the_socket(clean_env):
    clean_env.setenv("TASKMAN_DATABASE_URL", "   ")
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/nemos-infra:us-central1:nemos-pg")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_staging")
    clean_env.setenv("TASKMAN_DB_USER", "taskman_staging")
    clean_env.setenv("TASKMAN_DB_PASSWORD", "p@ss w/rd'x")
    info = conninfo_to_dict(database_url())
    assert info["host"] == "/cloudsql/nemos-infra:us-central1:nemos-pg"
    assert info["dbname"] == "taskman_staging"
    assert info["user"] == "taskman_staging"
    assert info["password"] == "p@ss w/rd'x"


def test_socket_password_strips_a_trailing_newline(clean_env):
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/nemos-infra:us-central1:nemos-pg")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_prod")
    clean_env.setenv("TASKMAN_DB_USER", "taskman_prod")
    clean_env.setenv("TASKMAN_DB_PASSWORD", "secret\n")
    assert conninfo_to_dict(database_url())["password"] == "secret"


def test_missing_database_settings_name_both_options(clean_env):
    with pytest.raises(RuntimeError, match="TASKMAN_DATABASE_URL") as caught:
        database_url()
    message = str(caught.value)
    assert "TASKMAN_DB_SOCKET" in message
    assert "TASKMAN_DB_PASSWORD" in message


def test_incomplete_socket_settings_name_only_the_missing_ones(clean_env):
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/nemos-infra:us-central1:nemos-pg")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_staging")
    with pytest.raises(RuntimeError, match="TASKMAN_DB_USER") as caught:
        database_url()
    message = str(caught.value)
    assert "TASKMAN_DB_PASSWORD" in message
    assert "TASKMAN_DB_SOCKET" not in message.split("also set:", 1)[1]


def test_whitespace_password_is_missing(clean_env):
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/instance")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_staging")
    clean_env.setenv("TASKMAN_DB_USER", "taskman_staging")
    clean_env.setenv("TASKMAN_DB_PASSWORD", "  \n")
    with pytest.raises(RuntimeError, match="TASKMAN_DB_PASSWORD"):
        database_url()


def test_serve_config_defaults_to_port_8080_from_socket_parts(clean_env):
    clean_env.setenv("TASKMAN_ACCESS_TOKEN", "token")
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/nemos-infra:us-central1:nemos-pg")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_staging")
    clean_env.setenv("TASKMAN_DB_USER", "taskman_staging")
    clean_env.setenv("TASKMAN_DB_PASSWORD", "secret")
    config = serve_config()
    assert config.port == 8080
    assert config.bind == "0.0.0.0:8080"


def test_serve_config_reports_incomplete_socket_settings(clean_env):
    clean_env.setenv("TASKMAN_ACCESS_TOKEN", "token")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_staging")
    with pytest.raises(ServeConfigError, match="TASKMAN_DB_SOCKET"):
        serve_config()


def test_migrate_command_uses_the_socket_connection(clean_env, monkeypatch):
    clean_env.setenv("TASKMAN_DB_SOCKET", "/cloudsql/nemos-infra:us-central1:nemos-pg")
    clean_env.setenv("TASKMAN_DB_NAME", "taskman_prod")
    clean_env.setenv("TASKMAN_DB_USER", "taskman_prod")
    clean_env.setenv("TASKMAN_DB_PASSWORD", "s3cret")
    seen = {}
    monkeypatch.setattr(
        "taskman.commands.serve.wait_for_database",
        lambda url, attempts=30, delay=1.0: seen.setdefault("wait", url),
    )
    monkeypatch.setattr(
        "taskman.commands.serve.migrate",
        lambda url=None: seen.setdefault("migrate", url),
    )
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "Database schema is up to date." in result.output
    assert seen["wait"] == seen["migrate"]
    info = conninfo_to_dict(seen["migrate"])
    assert info["host"] == "/cloudsql/nemos-infra:us-central1:nemos-pg"
    assert info["dbname"] == "taskman_prod"
    assert info["user"] == "taskman_prod"
    assert info["password"] == "s3cret"


def test_migrate_command_reports_missing_settings(clean_env):
    result = runner.invoke(app, ["migrate"])
    text = (result.output or "") + (result.stderr or "")
    assert result.exit_code == 1
    assert "TASKMAN_DATABASE_URL" in text


def test_migrate_applies_schema_without_an_access_token(database, monkeypatch):
    monkeypatch.delenv("TASKMAN_ACCESS_TOKEN", raising=False)
    result = runner.invoke(app, ["migrate"])
    text = (result.output or "") + (result.stderr or "")
    assert result.exit_code == 0, text
    assert "Database schema is up to date." in text
