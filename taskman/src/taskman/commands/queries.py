"""Graph-query commands: `ready`, `dependents <id>`, `waiting-on <id>`.

All three walk the dependency graph defined by ``depends_on:`` lists in
work-item frontmatter. They share a single tree-walk + graph-build pass
(``_load_graph``) and a single output formatter (``_emit``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import iter_work_items, parent_item_path
from taskman.model.names import NameParseError, parse_name, special_file_name
from taskman.model.yaml_io import FrontmatterError, read_file


@dataclass(frozen=True, slots=True)
class _Item:
    """Materialized work-item record used by query commands."""

    item_id: str
    item_type: str
    status: str
    priority: str
    slug: str
    title: str
    path: str
    depends_on: tuple[str, ...]
    draft: bool = False
    parent_id: str | None = None


def _load_graph(tasks_dir: Path) -> dict[str, _Item]:
    """Walk the tree once; return id → _Item mapping."""
    items: dict[str, _Item] = {}
    for path in iter_work_items(tasks_dir):
        try:
            wi = parse_name(path.name)
        except NameParseError:
            continue
        body_file = (
            path / special_file_name(wi.item_id) if path.is_dir() else path
        )
        if not body_file.exists():
            continue
        try:
            data, _ = read_file(body_file)
        except FrontmatterError:
            continue
        deps_raw = data.get("depends_on") or []
        deps = tuple(str(d) for d in deps_raw) if isinstance(deps_raw, list) else ()
        items[wi.item_id] = _Item(
            item_id=wi.item_id,
            item_type=wi.item_type,
            status=wi.status,
            priority=wi.priority,
            slug=wi.slug,
            title=str(data.get("title", "")),
            path=str(path),
            depends_on=deps,
            draft=bool(data.get("draft")),
            parent_id=parse_name(parent_item_path(path, tasks_dir).name).item_id if parent_item_path(path, tasks_dir) else None,
        )
    return items


def _as_dict(item: _Item) -> dict[str, str | bool | None | list[str]]:
    return {
        "id": item.item_id,
        "type": item.item_type,
        "status": item.status,
        "priority": item.priority,
        "slug": item.slug,
        "title": item.title,
        "path": item.path,
        "depends_on": list(item.depends_on),
        "draft": item.draft,
        "parent_id": item.parent_id,
    }


def _emit(results: list[_Item], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps([_as_dict(i) for i in results], indent=2))
        return
    if not results:
        typer.echo("(none)")
        return
    for i in results:
        typer.echo(
            f"  {i.priority} {i.item_type:5} {i.item_id} {i.status:7} "
            f"{i.slug:30} {i.title}"
        )


def is_ready(item: _Item, items: dict[str, _Item]) -> bool:
    if item.status == "done":
        return False
    current = item
    while current:
        if current.draft or current.status == "done":
            return False
        if any(d not in items or items[d].status != "done" for d in current.depends_on):
            return False
        current = items.get(current.parent_id)
    return True


def ready(
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List items whose every dependency is in done (or who have no deps)."""
    items = _load_graph(get_tasks_dir())
    out: list[_Item] = []
    for item in items.values():
        if is_ready(item, items):
            out.append(item)
    out.sort(key=lambda i: (i.priority, i.item_id))
    _emit(out, as_json)


def dependents(
    item_id: str = typer.Argument(..., help="Item ID to find dependents of."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List non-done items that list ``<id>`` in their depends_on."""
    items = _load_graph(get_tasks_dir())
    if item_id not in items:
        typer.echo(f"Warning: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    out = [
        i for i in items.values()
        if i.status != "done" and item_id in i.depends_on
    ]
    out.sort(key=lambda i: (i.priority, i.item_id))
    _emit(out, as_json)


def waiting_on(
    item_id: str = typer.Argument(..., help="Item ID whose deps to inspect."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List unfinished dependencies of this item and its ancestors."""
    items = _load_graph(get_tasks_dir())
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
    out.sort(key=lambda i: (i.priority, i.item_id))
    _emit(out, as_json)
