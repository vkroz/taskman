"""`close` command — flip an item's status to done.

For top-level items, also moves the subtree to ``done/``. For nested items,
only rewrites the in-name status token and YAML status field (the item
stays in its parent's directory).

Precondition: every descendant must already be in ``done`` status.
"""
from __future__ import annotations

from pathlib import Path

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import find_item_by_id, is_top_level_item
from taskman.model.names import (
    NameParseError,
    WorkItemName,
    emit_name,
    name_is_special_file,
    parse_name,
    special_file_name,
)
from taskman.model.yaml_io import read_file, write_file
from taskman.model.revisions import invalidate_ancestors


def _open_descendants(path: Path) -> list[Path]:
    """Return descendant work-item paths whose in-name status is not ``done``."""
    if not path.is_dir():
        return []
    open_items: list[Path] = []
    for child in path.rglob("*"):
        if name_is_special_file(child.name):
            continue
        try:
            wi = parse_name(child.name)
        except NameParseError:
            continue
        if wi.status != "done":
            open_items.append(child)
    return open_items


def close(
    item_id: str = typer.Argument(..., help="Item ID to close."),
) -> None:
    """Flip an item to status ``done``. Rejects if descendants are not all done."""
    tasks_dir = get_tasks_dir()
    path = find_item_by_id(tasks_dir, item_id)
    if path is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    wi = parse_name(path.name)
    if wi.status == "done":
        typer.echo(f"No change: {item_id} already done.")
        return

    open_descendants = _open_descendants(path)
    if open_descendants:
        typer.echo(
            f"Error: cannot close {item_id} — descendants not in done:",
            err=True,
        )
        for d in open_descendants:
            typer.echo(f"  {d.name}", err=True)
        raise typer.Exit(1)

    body_file = path / special_file_name(item_id) if path.is_dir() else path
    data, body = read_file(body_file)
    if data.get("draft"):
        raise typer.BadParameter("finalize the draft before closing it")
    from taskman.commands.queries import _load_graph, is_ready
    graph = _load_graph(tasks_dir)
    if not is_ready(graph[item_id], graph):
        raise typer.BadParameter("item or ancestor has an unfinished dependency or is a draft")
    entries = data.get("journal") or []
    revision = int(data.get("revision", 0))
    checks = [e for e in entries if e.get("kind") == "verification"]
    if not checks or checks[-1].get("result") != "pass" or checks[-1].get("revision") != revision:
        raise typer.BadParameter("record a passing verification for the current revision before closing")
    if not any(e.get("kind") == "summary" and e.get("revision") == revision for e in entries):
        raise typer.BadParameter("record a summary for the current revision before closing")

    is_dir = path.is_dir()
    new_wi = WorkItemName(
        priority=wi.priority,
        item_id=wi.item_id,
        item_type=wi.item_type,
        status="done",
        slug=wi.slug,
    )
    new_name = emit_name(new_wi, as_directory=is_dir)

    if is_top_level_item(path, tasks_dir):
        target_dir = tasks_dir / "done"
        target_dir.mkdir(parents=True, exist_ok=True)
        new_path = target_dir / new_name
    else:
        new_path = path.parent / new_name

    if new_path.exists():
        typer.echo(f"Error: target already exists: {new_path}", err=True)
        raise typer.Exit(1)

    path.rename(new_path)

    body_file = (
        new_path / special_file_name(item_id) if new_path.is_dir() else new_path
    )
    data, body = read_file(body_file)
    data["status"] = "done"
    write_file(body_file, data, body)
    invalidate_ancestors(tasks_dir, new_path)

    typer.echo(str(new_path))
