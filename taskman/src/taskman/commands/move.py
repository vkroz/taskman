"""Change item status, preserving nesting and enforcing completion gates."""
from __future__ import annotations

import typer

from taskman import lifecycle
from taskman.commands.outcome import reported
from taskman.storage.store import open_store

_TARGETS = ("backlog", "active", "done")


def move(
    item_id: str = typer.Argument(..., help="Item ID (9 digits)."),
    target_status: str = typer.Argument(..., help="One of: backlog, active, done."),
) -> None:
    """Change an item's status; nested items stay inside their parent."""
    if target_status not in _TARGETS:
        typer.echo(
            f"Error: target status must be one of {list(_TARGETS)}, got: {target_status!r}",
            err=True,
        )
        raise typer.Exit(2)
    with reported():
        item = lifecycle.move(open_store(), item_id, target_status)
    typer.echo(item.location)
