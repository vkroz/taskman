"""`list` command — walk the tree and print items, optionally filtered/JSON."""
from __future__ import annotations

import json

import typer

from taskman.storage.store import open_store, key_order


def list_items(
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    item_type: str | None = typer.Option(None, "--type", help="Filter by type."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
    parent: str | None = typer.Option(None, "--parent", help="List children of this ID."),
    recursive: bool = typer.Option(False, "--recursive", help="Include all descendants with --parent."),
    project: str | None = typer.Option(None, "--project", help="Only items of this project key."),
    assignee: str | None = typer.Option(None, "--assignee", help="Only items with this assignee."),
    label: str | None = typer.Option(None, "--label", help="Only items with this label."),
) -> None:
    """List work items in the tree, optionally filtered, optionally as JSON."""
    store = open_store()
    items = store.items()
    parents = {item.id: item.parent_id for item in items}
    if parent and parent not in parents:
        raise typer.BadParameter(f"parent not found: {parent}")

    def under_parent(item_id: str) -> bool:
        current = parents[item_id]
        while current:
            if current == parent:
                return True
            if not recursive:
                return False
            current = parents.get(current)
        return False

    rows: list[dict[str, str]] = []
    for item in items:
        if parent and not under_parent(item.id):
            continue
        if status is not None and item.status != status:
            continue
        if item_type is not None and item.type != item_type:
            continue
        if project is not None and item.metadata.get("project") != project:
            continue
        if assignee is not None and item.metadata.get("assignee") != assignee:
            continue
        if label is not None and label not in (item.metadata.get("labels") or []):
            continue
        rows.append(
            {
                "priority": item.priority,
                "type": item.type,
                "id": item.id,
                "status": item.status,
                "slug": item.slug,
                "title": item.title,
                "path": item.location,
                "parent_id": item.parent_id,
            }
        )
    rows.sort(key=lambda r: (r["status"], r["priority"], key_order(r["id"])))

    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        typer.echo("(no items)")
        return

    by_status: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_status.setdefault(row["status"], []).append(row)
    for s in ("backlog", "active", "done"):
        if s not in by_status:
            continue
        typer.echo(f"\n[{s.upper()}]")
        for row in by_status[s]:
            typer.echo(
                f"  {row['priority']} {row['type']:5} {row['id']} "
                f"{row['slug']:30} {row['title']}"
            )
