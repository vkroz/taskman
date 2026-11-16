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


def test_help_says_how_list_orders_items() -> None:
    assert "List items in the order set in the UI or with `reorder`" in " ".join(_help().split())


def test_help_documents_server_mode_and_the_proxy_token() -> None:
    text = _help()
    storage = text.split("ITEMS AND KEYS")[0]
    assert "With TASKMAN_SERVER_URL set, or after `taskman auth login`" in storage
    assert "open no database" in storage and "`taskman auth status`" in storage
    assert "TASKMAN_TOKEN=proxy-injected" in text


def test_help_lists_the_query_and_edit_commands() -> None:
    lifecycle = _help().split("LIFECYCLE")[1].split("INVARIANTS")[0]
    for name in ("ready", "edit", "browse", "search", "waiting-on", "dependents"):
        assert f"\n  {name} " in lifecycle or f"\n  {name}\n" in lifecycle, name


def test_help_documents_the_error_for_an_unknown_key() -> None:
    assert "every command prints `Error: item not found: KEY` and exits with code 1" in " ".join(_help().split())
