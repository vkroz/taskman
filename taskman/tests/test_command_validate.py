"""Tests for the validate command."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from tests import sql_helpers as sql

runner = CliRunner()


# ---- happy path ----


def test_validate_empty_database(sqlite_env) -> None:
    """An empty database validates clean."""
    assert "OK" in sql.run("validate")


def test_validate_clean_tree(sqlite_env) -> None:
    """Items built only by `new` validate clean."""
    epic = sql.new("Epic", "epic")
    sql.new("Child", parent=epic)
    assert "OK" in sql.run("validate")


def test_validate_json_clean(sqlite_env) -> None:
    data = json.loads(sql.run("validate", "--json"))
    assert data["ok"] is True
    assert data["findings"] == []
