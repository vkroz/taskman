"""Smoke tests: package imports, CLI scaffolding works."""
from __future__ import annotations

from typer.testing import CliRunner

from taskman import __version__
from taskman.cli import app

runner = CliRunner()


def test_version_is_nonempty_string() -> None:
    """Package exposes a non-empty version string."""
    assert isinstance(__version__, str)
    assert __version__


def test_cli_help_exits_zero() -> None:
    """`taskman --help` exits 0 and prints typer's usage text."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"output:\n{result.output}"
    assert "Usage" in result.output


def test_cli_no_args_shows_help() -> None:
    """`taskman` with no args invokes typer's no_args_is_help behavior."""
    result = runner.invoke(app, [])
    # no_args_is_help=True exits non-zero (typer convention) but prints help.
    assert "Usage" in result.output


def test_version_command_prints_version() -> None:
    """`taskman version` prints the package version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, f"output:\n{result.output}"
    assert __version__ in result.output
