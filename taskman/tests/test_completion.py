"""Tests for shell completion.

Guards two things:
1. Completion stays enabled on the Typer app (the one-line fix in cli.py) —
   without it, ``taskman --install-completion`` disappears.
2. ``taskman --install-completion <shell>`` still writes a usable completion
   file. This is the path end users run, so it fails loudly if a Typer upgrade
   changes the install behavior. Hermetic — no real shell needed.
"""
from __future__ import annotations

from typer.testing import CliRunner

from taskman.cli import app

runner = CliRunner()


def test_completion_enabled() -> None:
    """add_completion must stay on, or --install-completion disappears."""
    assert app._add_completion is True


def test_install_completion_writes_files(tmp_path, monkeypatch) -> None:
    """`taskman --install-completion zsh` creates the completion stub + rc line."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Pass the shell explicitly instead of auto-detecting (CI has no real shell).
    monkeypatch.setenv("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION", "1")
    result = runner.invoke(app, ["--install-completion", "zsh"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".zfunc" / "_taskman").is_file()
    assert "fpath" in (tmp_path / ".zshrc").read_text()
