"""Every command that takes an item key reports an unknown key the same way, locally and through a server."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from tests.sql_helpers import new

runner = CliRunner()

# Each command that takes an item key, with the other arguments it needs. ``KEY`` stands for the key.
KEYED_COMMANDS = [
    ["show", "KEY"],
    ["context", "KEY"],
    ["activity", "KEY"],
    ["dependents", "KEY"],
    ["waiting-on", "KEY"],
    ["update", "KEY", "--body-file", "-"],
    ["comment", "KEY", "--kind", "decision", "--body", "Chose X."],
    ["record", "KEY", "--kind", "decision", "--body", "Chose X."],
    ["assign", "KEY", "codex"],
    ["unassign", "KEY"],
    ["label", "KEY", "--add", "ui"],
    ["depend", "KEY", "--on", "TM-1"],
    ["depend", "KEY", "--clear"],
    ["discard", "KEY"],
    ["edit", "KEY", "--title", "New", "--reason", "Clearer."],
    ["reorder", "KEY", "--position", "1"],
    ["move", "KEY", "active"],
    ["close", "KEY"],
    ["finalize", "KEY"],
    ["convert", "KEY", "feat"],
]


@pytest.fixture(params=["local", "server"])
def store_env(request):
    """A local SQLite database, or a Taskman server over one."""
    return request.getfixturevalue("sqlite_env" if request.param == "local" else "remote_env")


@pytest.mark.parametrize("command", KEYED_COMMANDS, ids=lambda command: " ".join(command[:2]))
def test_an_unknown_key_prints_one_error_line_and_exits_1(store_env, command):
    new("Known")
    result = runner.invoke(app, [("TM-9" if arg == "KEY" else arg) for arg in command], input="Spec.\n")
    assert result.exit_code == 1, result.output
    assert result.stderr == "Error: item not found: TM-9\n"
    assert result.stdout == ""


@pytest.mark.parametrize("command", KEYED_COMMANDS, ids=lambda command: " ".join(command[:2]))
def test_a_known_key_passes_the_check(store_env, command):
    new("Known")
    result = runner.invoke(app, [("TM-1" if arg == "KEY" else arg) for arg in command], input="Spec.\n")
    assert "item not found" not in result.output
