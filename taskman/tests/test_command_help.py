"""Tests for the help command."""
from __future__ import annotations

from typer.testing import CliRunner

from taskman.cli import app

runner = CliRunner()


def test_help_subcommand_exits_zero() -> None:
    r = runner.invoke(app, ["help"])
    assert r.exit_code == 0


def test_help_documents_storage_layout() -> None:
    r = runner.invoke(app, ["help"])
    assert "STORAGE LAYOUT" in r.output
    assert "tasks/" in r.output
    assert "backlog/" in r.output


def test_help_documents_name_grammar() -> None:
    r = runner.invoke(app, ["help"])
    assert "NAME GRAMMAR" in r.output
    assert "<priority>.<type>-<id>.<status>.<slug>" in r.output
    assert "_<id>.md" in r.output


def test_help_documents_id_scheme() -> None:
    r = runner.invoke(app, ["help"])
    assert "ID GENERATION" in r.output
    assert "YYMMDDnnn" in r.output


def test_help_documents_invariants() -> None:
    r = runner.invoke(app, ["help"])
    assert "task = leaf" in r.output
    assert "Free type ladder" in r.output


def test_help_documents_env_var() -> None:
    r = runner.invoke(app, ["help"])
    assert "TASKMAN_TASKS_DIR" in r.output
