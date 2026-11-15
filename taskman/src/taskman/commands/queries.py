"""Graph-query commands: `ready`, `dependents <id>`, `waiting-on <id>`.

All three walk the dependency graph defined by ``depends_on:`` lists in
work-item frontmatter. They share a single tree-walk + graph-build pass
(``_load_graph``) and a single output formatter (``_emit``).
"""
from __future__ import annotations

import json

import typer

from taskman.lifecycle import is_ready
from taskman.model.collection import in_manual_order
from taskman.storage.store import Item, Store, key_order, open_store


def _load_graph(store: Store) -> dict[str, Item]:
    """Read every item once; return key → Item."""
    return {item.id: item for item in store.items() if not item.error}


def _as_dict(item: Item) -> dict[str, str | bool | None | list[str]]:
    return {
        "id": item.id,
        "type": item.type,
        "status": item.status,
        "priority": item.priority,
        "slug": item.slug,
        "title": item.title,
        "path": item.location,
        "depends_on": list(item.depends_on),
        "draft": item.draft,
        "parent_id": item.parent_id,
    }


def _emit(results: list[Item], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps([_as_dict(i) for i in results], indent=2))
        return
    if not results:
        typer.echo("(none)")
        return
    for i in results:
        typer.echo(
            f"  {i.priority} {i.type:5} {i.id} {i.status:7} "
            f"{i.slug:30} {i.title}"
        )


_NOTE_LIMIT = 10


def _not_ready_reason(item: Item, items: dict[str, Item]) -> str:
    """Why an open item is not ready: a draft, under a draft, or waiting on dependencies."""
    if item.draft:
        return "draft"
    parent = items.get(item.parent_id)
    while parent:
        if parent.draft:
            return "under a draft"
        parent = items.get(parent.parent_id)
    return "waiting on dependencies"


def _not_ready_note(left_out: list[Item], items: dict[str, Item]) -> str:
    """One line that names the open items that ``ready`` leaves out, and why."""
    groups: dict[str, list[str]] = {"draft": [], "under a draft": [], "waiting on dependencies": []}
    for item in left_out[:_NOTE_LIMIT]:
        groups[_not_ready_reason(item, items)].append(item.id)
    named = "; ".join(f"{', '.join(keys)} ({reason})" for reason, keys in groups.items() if keys)
    more = f"; and {len(left_out) - _NOTE_LIMIT} more" if len(left_out) > _NOTE_LIMIT else ""
    return f"Not ready: {named}{more}. To see them, run: taskman list"


def ready(
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
    project: str | None = typer.Option(None, "--project", help="Only items of this project key."),
) -> None:
    """List open items that are ready to start.

    An item is ready when it is not done, and the item and all its ancestors are
    finalized, not done, and have no unfinished dependency. When open items are
    not ready, text output ends with one line on stderr that names them and gives
    the reason. Items show in the order that you set in the UI or with
    `taskman reorder`.
    """
    store = open_store()
    items = _load_graph(store)
    out: list[Item] = []
    left_out: list[Item] = []
    for item in items.values():
        if project is not None and item.metadata.get("project") != project:
            continue
        if is_ready(item, items):
            out.append(item)
        elif item.status != "done":
            left_out.append(item)
    _emit(in_manual_order(store, out), as_json)
    if left_out and not as_json:
        left_out.sort(key=lambda i: key_order(i.id))
        typer.echo(_not_ready_note(left_out, items), err=True)


def dependents(
    item_id: str = typer.Argument(..., help="Item ID to find dependents of."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List non-done items that list ``<id>`` in their depends_on.

    Items show in the order that you set in the UI or with `taskman reorder`.
    """
    store = open_store()
    items = _load_graph(store)
    if item_id not in items:
        typer.echo(f"Warning: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    out = [
        i for i in items.values()
        if i.status != "done" and item_id in i.depends_on
    ]
    _emit(in_manual_order(store, out), as_json)


def waiting_on(
    item_id: str = typer.Argument(..., help="Item ID whose deps to inspect."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List unfinished dependencies of this item and its ancestors.

    Items show in the order that you set in the UI or with `taskman reorder`.
    """
    store = open_store()
    items = _load_graph(store)
    if item_id not in items:
        typer.echo(f"Warning: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    target = items[item_id]
    dependencies = set()
    while target:
        dependencies.update(target.depends_on)
        target = items.get(target.parent_id)
    missing = dependencies - items.keys()
    if missing:
        raise typer.BadParameter("missing dependencies: " + ", ".join(sorted(missing)))
    out = [items[dep] for dep in dependencies if items[dep].status != "done"]
    _emit(in_manual_order(store, out), as_json)
