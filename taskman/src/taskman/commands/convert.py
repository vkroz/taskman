"""`convert` command — relabel a work item's type.

Type tokens (``epic``/``feat``/``task``) are visual labels and convertible.
The only constraint is the ``task = leaf`` invariant: an item that has
children cannot become ``task``.
"""
from __future__ import annotations

import typer

from taskman import lifecycle
from taskman.commands.outcome import reported
from taskman.storage.store import open_store

_TYPES = ("epic", "feat", "task")


def convert(
    item_id: str = typer.Argument(..., help="Item ID or key, for example TM-42."),
    target_type: str = typer.Argument(..., help="One of: epic, feat, task."),
) -> None:
    """Relabel a work item's type. ``task`` requires no children (leaf invariant)."""
    if target_type not in _TYPES:
        typer.echo(
            f"Error: target type must be one of {list(_TYPES)}, got: {target_type!r}",
            err=True,
        )
        raise typer.Exit(2)
    with reported():
        item = lifecycle.convert(open_store(), item_id, target_type)
    typer.echo(item.location)
