"""Tests for the help command."""
from __future__ import annotations

from typer.testing import CliRunner

from taskman.cli import app

runner = CliRunner()


def _help() -> str:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    return result.output


def test_help_documents_sql_storage_and_keys() -> None:
    text = _help()
    assert "STORAGE" in text and "taskman db upgrade" in text
    assert "TM-42" in text and "--project KEY" in text


def test_help_documents_invariants() -> None:
    text = _help()
    assert "task = leaf" in text
    assert "Free type ladder" in text


def test_help_documents_every_storage_setting() -> None:
    text = _help()
    for name in ("TASKMAN_BACKEND", "TASKMAN_DATABASE_URL", "TASKMAN_DB_SOCKET", "TASKMAN_DB_NAME",
                 "TASKMAN_DB_USER", "TASKMAN_DB_PASSWORD", "TASKMAN_DB_PASSWORD_FILE"):
        assert name in text


def test_help_describes_only_the_sql_model() -> None:
    text = _help()
    for removed in ("TASKMAN_TASKS_DIR", "file backend", "YYMMDD", "consolidat", "_<id>.md", "backlog/"):
        assert removed not in text
