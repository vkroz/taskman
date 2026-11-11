"""CLI access to the same queries, editing and ordering operations as the UI."""

import json
import typer
from taskman import lifecycle
from taskman.commands.context import read_body
from taskman.model.collection import collection, query
from taskman.model.ordering import reorder_item
from taskman.storage.store import open_store


def edit_item(store, item_id, title, body, reason):
    """Edit a title and description and journal the reason. Raises ValueError on a rule failure."""
    lifecycle.edit(store, item_id, title, body, reason)


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
                collection(open_store()),
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


def reorder(
    item_id: str = typer.Argument(..., help="Item ID to move."),
    position: int = typer.Option(..., "--position", min=1, help="One-based sibling position."),
):
    """Move an item to a persisted position among its siblings; a top-level item stays in its project."""
    try:
        order = reorder_item(open_store(), item_id, position)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps({"id": item_id, "position": position, "order": order}, indent=2))
