"""Tests for the help command."""
from __future__ import annotations

import pytest
import typer.main
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


def test_help_documents_self_service_tokens() -> None:
    text = " ".join(_help().split())
    assert "A token is valid for 90 days." in text
    assert "`taskman token list` shows your own tokens (ID, label, created, expires, last used, revoked)" in text
    assert "`taskman token revoke ID` revokes one of them, also the token in use." in text
    assert "/auth/token page shows the same list" in text and "Nobody sees or revokes another person's tokens" in text
    assert "With `--database`, or with no server set" in text


def test_help_lists_the_query_and_edit_commands() -> None:
    lifecycle = _help().split("LIFECYCLE")[1].split("INVARIANTS")[0]
    for name in ("ready", "edit", "browse", "search", "waiting-on", "dependents"):
        assert f"\n  {name} " in lifecycle or f"\n  {name}\n" in lifecycle, name


def test_help_describes_the_bugfix_type() -> None:
    text = _help()
    assert "epic | feat | task | bugfix" in text
    assert "A `task` or `bugfix` item has no children" in text


def test_help_documents_the_error_for_an_unknown_key() -> None:
    assert "every command prints `Error: item not found: KEY` and exits with code 1" in " ".join(_help().split())


def test_help_gives_the_default_author_on_a_server_and_with_a_local_database() -> None:
    text = " ".join(_help().split())
    assert "Without it, the author is the person of the token on a server, or the git user.name with a local database." in text
    assert "--actor NAME gives the author `NAME for <person>`" in text


def _commands_with_actor() -> list[str]:
    group = typer.main.get_command(app)
    return [name for name, command in group.commands.items()
            if not hasattr(command, "commands") and any("--actor" in p.opts for p in command.params)]


def test_the_commands_with_actor_include_the_writes() -> None:
    names = _commands_with_actor()
    for name in ("new", "finalize", "move", "close", "comment", "record", "edit", "assign", "depend", "discard"):
        assert name in names, name


@pytest.mark.parametrize("command", _commands_with_actor())
def test_every_actor_help_gives_the_default_on_a_server_and_with_a_local_database(command) -> None:
    result = runner.invoke(app, [command, "--help"], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    line = next(line for line in result.output.splitlines() if "--actor" in line)
    assert "Default: the person of the token on a server, the git user.name with a local database." in line
    assert "Default: git user.name" not in result.output
