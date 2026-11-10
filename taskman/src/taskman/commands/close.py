"""`close` command — flip an item's status to done.

For top-level items, also moves the subtree to ``done/``. For nested items,
only the item's own status changes; it stays inside its parent.

Precondition: every descendant must already be in ``done`` status, and the
item needs a current summary and passing verification.
"""
from __future__ import annotations

import typer

from taskman import lifecycle
from taskman.commands.outcome import reported
from taskman.storage.store import open_store


def close(
    item_id: str = typer.Argument(..., help="Item ID to close."),
) -> None:
    """Flip an item to status ``done``. Rejects if descendants are not all done."""
    with reported():
        item = lifecycle.close(open_store(), item_id)
    typer.echo(item.location)
