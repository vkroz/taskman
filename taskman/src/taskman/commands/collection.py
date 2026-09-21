"""CLI access to the same queries, editing and history operations as the UI."""

import json
import typer
from taskman.commands.new import get_tasks_dir
from taskman.commands.context import load_item, read_body
from taskman.model.collection import (
    collection,
    query,
    consolidate as do_consolidate,
    histories,
)
from taskman.model.yaml_io import write_file
from taskman.model.revisions import invalidate_ancestors


def edit_item(tasks_dir, item_id, title, body, reason):
    path, file, data, _ = load_item(tasks_dir, item_id)
    if data.get("status") == "done":
        raise ValueError("Completed work is immutable; create a follow-up task")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Enter a title of 1–200 characters")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Description must not be empty")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Explain why the task changed")
    from datetime import datetime, timezone

    data["title"] = title.strip()
    data["revision"] = int(data.get("revision", 0)) + 1
    data.setdefault("journal", []).append(
        {
            "kind": "decision",
            "actor": "Taskman user",
            "at": datetime.now(timezone.utc).isoformat(),
            "text": reason.strip(),
            "revision": data["revision"],
        }
    )
    write_file(file, data, body)
    invalidate_ancestors(tasks_dir, path)


def edit(
    item_id: str,
    title: str = typer.Option(...),
    body_file: str = typer.Option(...),
    reason: str = typer.Option(...),
):
    """Edit an open title and description and journal the reason."""
    try:
        edit_item(get_tasks_dir(), item_id, title, read_body(body_file), reason)
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
    typer.echo(
        json.dumps(
            query(
                collection(get_tasks_dir(), include_archived),
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
        result = do_consolidate(get_tasks_dir(), ids, title)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(json.dumps(result, indent=2))


def history():
    """Retrieve consolidation summaries and original task IDs."""
    typer.echo(json.dumps(histories(get_tasks_dir()), indent=2))
