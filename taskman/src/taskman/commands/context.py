"""Shared context, body updates, dependencies, and evidence for any agent."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import typer

from taskman import lifecycle
from taskman.commands.outcome import reported
from taskman.storage.store import open_store


def read_body(source: str) -> str:
    try:
        return sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(str(exc)) from exc


def update(
    item_id: str = typer.Argument(...),
    body_file: str = typer.Option(..., "--body-file", help="Replace Markdown body from file; - for stdin."),
) -> None:
    """Replace the specification/body while preserving managed metadata and journal."""
    body = read_body(body_file)
    with reported():
        item = lifecycle.update_body(open_store(), item_id, body)
    typer.echo(item.id)


def comment(
    item_id: str = typer.Argument(...),
    kind: str = typer.Option(..., "--kind", help="decision, progress, handoff, summary, or verification."),
    body: str | None = typer.Option(None, "--body", help="The comment text. Give this or --body-file."),
    body_file: str | None = typer.Option(None, "--body-file", help="Evidence or context; - reads stdin."),
    result: str | None = typer.Option(None, "--result", help="Verification only: pass, fail, or blocked."),
    actor: str | None = typer.Option(None, "--actor", help="Author of the comment. Default: the person of the token on a server, the git user.name with a local database."),
) -> None:
    """Add an attributed comment without replacing another agent's context. `record` is an alias."""
    if (body is None) == (body_file is None):
        raise typer.BadParameter("give exactly one of --body and --body-file")
    store = open_store()
    author = actor or store.actor
    with reported():
        lifecycle.check_entry(kind, author, result)
    text = body if body is not None else read_body(body_file)
    with reported():
        lifecycle.record(store, item_id, kind, author, text, result)
    typer.echo(f"Recorded {kind}: {item_id}")


def assign(
    item_id: str = typer.Argument(...),
    name: str = typer.Argument(..., help="Person or agent name, for example claude-code."),
) -> None:
    """Set the assignee of an open item."""
    with reported():
        item = lifecycle.assign(open_store(), item_id, name)
    typer.echo(f"Assigned {item_id} to {item.metadata['assignee']}")


def unassign(item_id: str = typer.Argument(...)) -> None:
    """Remove the assignee of an open item."""
    with reported():
        lifecycle.assign(open_store(), item_id, None)
    typer.echo(f"Unassigned: {item_id}")


def label(
    item_id: str = typer.Argument(...),
    add: list[str] | None = typer.Option(None, "--add", help="Label to add; repeat for more."),
    remove: list[str] | None = typer.Option(None, "--remove", help="Label to remove; repeat for more."),
) -> None:
    """Add or remove labels of an open item."""
    if not add and not remove:
        raise typer.BadParameter("supply --add or --remove labels")
    with reported():
        item = lifecycle.label(open_store(), item_id, add or [], remove or [])
    typer.echo(f"Labels of {item_id}: {', '.join(item.metadata['labels']) or 'none'}")


def repo(
    item_id: str = typer.Argument(...),
    addresses: list[str] | None = typer.Argument(None, help="Remote addresses, for example github.com/OWNER/NAME."),
    clear: bool = typer.Option(False, "--clear", help="Remove all repositories of the item."),
) -> None:
    """Show, replace or clear the repositories of an item. Without own repositories, an item inherits them."""
    if addresses and clear:
        raise typer.BadParameter("give addresses or --clear, not both")
    store = open_store()
    if addresses or clear:
        with reported():
            item = lifecycle.set_repositories(store, item_id, addresses or [])
        typer.echo(f"Repositories of {item_id}: {', '.join(item.metadata.get('repositories') or []) or 'none'}")
        return
    items = {item.id: item for item in store.items()}
    own = items[item_id].metadata.get("repositories") or []
    inherited = lifecycle.inherited_repositories(items[item_id], items)
    if own:
        typer.echo(f"Repositories of {item_id}: {', '.join(own)}")
    elif inherited:
        typer.echo(f"Repositories of {item_id}, inherited from {inherited['from']}: {', '.join(inherited['addresses'])}")
    else:
        typer.echo(f"Repositories of {item_id}: none")


def activity(
    item_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show the field changes of an item, oldest first."""
    store = open_store()
    if store.get(item_id) is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    entries = store.activity(item_id)
    if as_json:
        typer.echo(json.dumps(entries, indent=2))
        return
    for entry in entries:
        change = entry["new"] if entry["field"] == "created" else f"{entry['old'] or '-'} -> {entry['new'] or '-'}"
        if entry["field"] == "description":
            change = "changed"
        typer.echo(f"{entry['at']}  {entry['author']}  {entry['field']}: {change}")


def depend(
    item_id: str = typer.Argument(...),
    on: list[str] | None = typer.Option(None, "--on", help="Dependency ID; repeat to set multiple dependencies."),
    clear: bool = typer.Option(False, "--clear", help="Remove all dependencies."),
) -> None:
    """Replace dependencies, rejecting unknown IDs and cycles before writing."""
    if bool(on) == clear:
        raise typer.BadParameter("supply --on IDs or --clear")
    with reported():
        lifecycle.set_dependencies(open_store(), item_id, on or [])
    typer.echo(f"Updated dependencies: {item_id}")


def discard(item_id: str = typer.Argument(...)) -> None:
    """Remove a draft leaf with no incoming dependencies."""
    with reported():
        lifecycle.discard(open_store(), item_id)
    typer.echo(f"Discarded: {item_id}")


# Only the form that Taskman prints: no leading zeros, nothing after the second number.
_CURSOR = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z")


def _cursor(item, activity: list) -> str:
    """The read position: the number of comments and of field changes. Both only grow."""
    return f"{len(item.metadata.get('journal') or [])}.{len(activity)}"


def _news(store, item_id: str, since: str) -> dict:
    """The comments and field changes of an item after ``since``, and the new cursor."""
    match = _CURSOR.match(since)
    item = store.get(item_id)
    activity = store.activity(item_id)
    comments = item.metadata.get("journal") or []
    if not match or int(match[1]) > len(comments) or int(match[2]) > len(activity):
        raise typer.BadParameter(f"unknown cursor {since!r}; run `taskman context {item_id}` for a new one")
    revision = item.metadata.get("revision", 0)
    changes = []
    for entry in activity[int(match[2]):]:
        if entry["field"] == "description":
            # The text can be long: an agent reads the item again when it needs it.
            entry = {"field": "description", "author": entry["author"], "at": entry["at"],
                     "note": f"changed; the current revision of the item is {revision}"}
        changes.append(entry)
    return {"id": item_id, "cursor": _cursor(item, activity), "revision": revision, "status": item.status,
            "draft": item.draft, "comments": comments[int(match[1]):], "changes": changes}


def context(
    item_id: str = typer.Argument(...),
    since: str | None = typer.Option(
        None, "--since", help="Only the comments and changes after this cursor. Each output gives the next cursor."
    ),
) -> None:
    """Emit task, ancestors, descendants, dependencies and the item's activity as JSON for a fresh agent.

    The output gives a cursor. With --since CURSOR, the output holds only the item's comments and field changes after that cursor, and a description change shows as a note without its text. When nothing is new, the output is one line.
    """
    store = open_store()
    if since is not None:
        news = _news(store, item_id, since)
        quiet = not news["comments"] and not news["changes"]
        typer.echo(json.dumps(news, indent=None if quiet else 2, default=str))
        return
    items = {item.id: item for item in store.items()}
    if item_id not in items:
        raise typer.BadParameter(f"item not found: {item_id}")

    def payload(item):
        return {"id": item.id, "parent_id": item.parent_id, "path": item.location, "frontmatter": item.metadata, "body": item.body}

    def ancestor_ids(item):
        parent = item.parent_id
        while parent in items:
            yield parent
            parent = items[parent].parent_id

    ancestors = [payload(items[parent]) for parent in ancestor_ids(items[item_id])]
    item = payload(items[item_id]) | {"inherited_repositories": lifecycle.inherited_repositories(items[item_id], items)}
    descendants = [payload(other) for other in items.values() if item_id in ancestor_ids(other)]
    dependencies = []
    seen = set()
    pending = [d for p in [item, *ancestors, *descendants] for d in p["frontmatter"].get("depends_on", [])]
    while pending:
        dep = str(pending.pop(0))
        if dep in seen:
            continue
        seen.add(dep)
        if dep in items:
            value = payload(items[dep])
            dependencies.append(value)
            pending.extend(value["frontmatter"].get("depends_on", []))
        else:
            dependencies.append({"id": dep, "missing": True})
    activity = store.activity(item_id)
    result = {"item": item, "ancestors": ancestors, "descendants": descendants, "dependencies": dependencies,
              "activity": activity, "cursor": _cursor(items[item_id], activity)}
    typer.echo(json.dumps(result, indent=2, default=str))
