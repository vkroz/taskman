"""`new` and `finalize` commands.

``new`` creates a work item — top-level or nested under a parent.
``finalize`` clears the ``draft: true`` marker on an item after the user
(or agent) has reviewed and approved the auto-generated stub.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from taskman import lifecycle
from taskman.commands.outcome import reported
from taskman.lifecycle import LifecycleError as NewCommandError  # noqa: F401 — kept for existing callers
from taskman.lifecycle import slugify  # noqa: F401 — kept for existing callers
from taskman.storage.file import get_tasks_dir  # noqa: F401 — kept for existing callers
from taskman.storage.store import open_store


def new(
    title: str = typer.Option(..., "--title", help="Human title."),
    item_type: str = typer.Option("task", "--type", help="One of: epic, feat, task."),
    parent: str | None = typer.Option(
        None,
        "--parent",
        help="Parent item ID (9 digits). Omit for a top-level item.",
    ),
    body_file: str | None = typer.Option(None, "--body-file", help="Markdown body file; - reads stdin."),
    as_json: bool = typer.Option(False, "--json", help="Emit ID and path as JSON."),
) -> None:
    """Create a new work item (draft)."""
    if item_type not in ("epic", "feat", "task"):
        typer.echo(
            f"Error: --type must be one of epic|feat|task, got: {item_type!r}",
            err=True,
        )
        raise typer.Exit(2)
    try:
        body = (sys.stdin.read() if body_file == "-" else Path(body_file).read_text(encoding="utf-8")) if body_file else None
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    with reported():
        item = lifecycle.create(open_store(), title, item_type, parent, body)
    typer.echo(json.dumps({"id": item.id, "path": item.location}) if as_json else item.location)


def finalize(
    item_id: str = typer.Argument(..., help="Item ID to clear the draft marker on."),
) -> None:
    """Clear ``draft: true`` on an item."""
    with reported():
        lifecycle.finalize(open_store(), item_id)
    typer.echo(f"Finalized: {item_id}")
