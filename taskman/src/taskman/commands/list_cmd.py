"""`list` command — walk the tree and print items, optionally filtered/JSON."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import iter_work_items, find_item_by_id, parent_item_path
from taskman.model.names import NameParseError, parse_name, special_file_name
from taskman.model.yaml_io import read_file


def _read_title(path: Path, item_id: str) -> str:
    """Return the title from a work item's body file, or empty on failure."""
    body_file = path / special_file_name(item_id) if path.is_dir() else path
    if not body_file.exists():
        return ""
    try:
        data, _ = read_file(body_file)
        return str(data.get("title", ""))
    except Exception:
        return ""


def list_items(
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    item_type: str | None = typer.Option(None, "--type", help="Filter by type."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
    parent: str | None = typer.Option(None, "--parent", help="List children of this ID."),
    recursive: bool = typer.Option(False, "--recursive", help="Include all descendants with --parent."),
) -> None:
    """List work items in the tree, optionally filtered, optionally as JSON."""
    from taskman.model.collection import histories
    tasks_dir = get_tasks_dir()
    archived = {id for h in histories(tasks_dir) for id in h["ids"]}
    parent_path = find_item_by_id(tasks_dir, parent) if parent else None
    if parent and parent_path is None:
        raise typer.BadParameter(f"parent not found: {parent}")
    rows: list[dict[str, str]] = []
    for path in iter_work_items(tasks_dir):
        if parent_path and not (path != parent_path and (path.is_relative_to(parent_path) if recursive else path.parent == parent_path)):
            continue
        try:
            wi = parse_name(path.name)
        except NameParseError:
            continue
        if wi.item_id in archived:
            continue
        if status is not None and wi.status != status:
            continue
        if item_type is not None and wi.item_type != item_type:
            continue
        rows.append(
            {
                "priority": wi.priority,
                "type": wi.item_type,
                "id": wi.item_id,
                "status": wi.status,
                "slug": wi.slug,
                "title": _read_title(path, wi.item_id),
                "path": str(path),
                "parent_id": parse_name(parent_item_path(path, tasks_dir).name).item_id if parent_item_path(path, tasks_dir) else None,
            }
        )
    rows.sort(key=lambda r: (r["status"], r["priority"], r["id"]))

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
