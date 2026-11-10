"""CLI helpers for tests on the SQLite database of the ``sqlite_env`` fixture."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from taskman.cli import app

runner = CliRunner()


def run(*args, input: str | None = None, code: int | None = 0) -> str:
    """Run a CLI command; check its exit code unless ``code`` is None. Returns stdout and stderr."""
    result = runner.invoke(app, [str(a) for a in args], input=input)
    if code is not None:
        assert result.exit_code == code, result.output
    return result.output


def new(title: str, item_type: str = "task", parent: str | None = None, project: str = "TM", body: str | None = None) -> str:
    """Create an item and return its key. A top-level item goes into ``project``."""
    args = ["new", "--title", title, "--type", item_type, "--json"]
    args += ["--parent", parent] if parent else ["--project", project]
    if body is not None:
        args += ["--body-file", "-"]
    return json.loads(run(*args, input=body))["id"]


def comment(key: str, kind: str, text: str = "Checked.", result: str | None = None, actor: str = "pytest") -> None:
    args = ["comment", key, "--kind", kind, "--actor", actor, "--body-file", "-"]
    run(*args, *(["--result", result] if result else []), input=text)


def prepare(key: str) -> None:
    """Finalize the item and add a current summary and passing verification, so it can close."""
    run("finalize", key, code=None)
    comment(key, "summary", "Fixture summary.")
    comment(key, "verification", "Fixture verification.", result="pass")


def show(key: str) -> dict:
    """The item's ``show --json`` payload: frontmatter, body and path."""
    return json.loads(run("show", key, "--json"))
