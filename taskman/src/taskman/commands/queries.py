"""Graph-query commands: `ready`, `dependents <id>`, `waiting-on <id>`.

All three walk the dependency graph defined by ``depends_on:`` lists in
work-item frontmatter. They share a single tree-walk + graph-build pass
(``_load_graph``) and a single output formatter (``_emit``).
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from taskman.lifecycle import is_ready
from taskman.storage.store import Item, Store, as_store, open_store


def _load_graph(source: Store | Path) -> dict[str, Item]:
    """Read every item once; return id → Item. Unreadable items are skipped."""
    return {item.id: item for item in as_store(source).items() if not item.error}


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


def ready(
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List items whose every dependency is in done (or who have no deps)."""
    items = _load_graph(open_store())
    out: list[Item] = []
    for item in items.values():
        if is_ready(item, items):
            out.append(item)
    out.sort(key=lambda i: (i.priority, i.id))
    _emit(out, as_json)


def dependents(
    item_id: str = typer.Argument(..., help="Item ID to find dependents of."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List non-done items that list ``<id>`` in their depends_on."""
    items = _load_graph(open_store())
    if item_id not in items:
        typer.echo(f"Warning: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    out = [
        i for i in items.values()
        if i.status != "done" and item_id in i.depends_on
    ]
    out.sort(key=lambda i: (i.priority, i.id))
    _emit(out, as_json)


def waiting_on(
    item_id: str = typer.Argument(..., help="Item ID whose deps to inspect."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List unfinished dependencies of this item and its ancestors."""
    items = _load_graph(open_store())
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
    out.sort(key=lambda i: (i.priority, i.id))
    _emit(out, as_json)
