"""CLI access to the same queries, editing and history operations as the UI."""

import json
import typer
from taskman import lifecycle
from taskman.commands.context import read_body
from taskman.model.collection import (
    collection,
    query,
    consolidate as do_consolidate,
    histories,
)
from taskman.model.ordering import reorder_item
from taskman.storage.store import as_store, open_store


def edit_item(source, item_id, title, body, reason):
    """Edit a title and description and journal the reason. Raises ValueError on a rule failure."""
    lifecycle.edit(as_store(source), item_id, title, body, reason)


def edit(
    item_id: str,
    title: str = typer.Option(...),
    body_file: str = typer.Option(...),
    reason: str = typer.Option(...),
):
    """Edit an open title and description and journal the reason."""
    try:
        edit_item(open_store(), item_id, title, read_body(body_file), reason)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(f"Updated: {item_id}")


def browse(
    where: list[str] = typer.Option(
        None, "--where", help="FIELD=VALUE exact; comma values OR. Repeat for AND."
    ),
    contains: list[str] = typer.Option(
        None, "--contains", help="FIELD=TEXT substring, including nested metadata."
    ),
    exists: list[str] = typer.Option(None, "--exists"),
    missing: list[str] = typer.Option(
        None, "--missing", help="Match metadata fields that are absent."
    ),
    keyword: str = typer.Option(""),
    epic: str = typer.Option(""),
    sort: str = typer.Option("priority"),
    descending: bool = typer.Option(False),
    include_archived: bool = typer.Option(False),
    project: str | None = typer.Option(None, "--project", help="Only items of this project key."),
):
    """Query all metadata and emit hierarchy-preserving JSON with context markers."""
    filters = []
    for op, values in [("eq", where), ("contains", contains)]:
        for value in values or []:
            if "=" not in value:
                raise typer.BadParameter("Use FIELD=VALUE")
            key, val = value.split("=", 1)
            filters.append({"field": key, "op": op, "value": val})
    filters += [{"field": key, "op": "exists", "value": "true"} for key in exists or []]
    filters += [
        {"field": key, "op": "exists", "value": "false"} for key in missing or []
    ]
    if project is not None:
        filters.append({"field": "project", "op": "eq", "value": project})
    typer.echo(
        json.dumps(
            query(
                collection(open_store(), include_archived),
                filters,
                keyword,
                epic,
                sort,
                descending,
            ),
            indent=2,
            default=str,
        )
    )


def consolidate(ids: list[str] = typer.Argument(...), title: str = typer.Option(...)):
    """Summarize completed subtrees; retain immutable originals and hide ordinary rows."""
    try:
        result = do_consolidate(open_store(), ids, title)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(json.dumps(result, indent=2))


def history():
    """Retrieve consolidation summaries and original task IDs."""
    store = open_store()
    if not store.consolidation:
        from taskman.storage.sql_store import NO_CONSOLIDATION

        raise typer.BadParameter(NO_CONSOLIDATION)
    typer.echo(json.dumps(histories(store), indent=2))


def reorder(
    item_id: str = typer.Argument(..., help="Item ID to move."),
    position: int = typer.Option(..., "--position", min=1, help="One-based sibling position."),
):
    """Move an item to a persisted position within its current parent."""
    try:
        order = reorder_item(open_store(), item_id, position)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps({"id": item_id, "position": position, "order": order}, indent=2))
