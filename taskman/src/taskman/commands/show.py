"""`show` command — print a work item's frontmatter and body."""
from __future__ import annotations

import json

import typer

from taskman.storage import open_store


def show(
    item_id: str = typer.Argument(..., help="Item ID."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Print a work item's frontmatter and body."""
    store = open_store()
    item = store.get(item_id)
    if item is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    if item.error:
        typer.echo(f"Error: {item.error}", err=True)
        raise typer.Exit(1)

    if as_json:
        payload = {
            "frontmatter": item.metadata,
            "body": item.body,
            "path": item.location,
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    typer.echo(store.document(item_id))
