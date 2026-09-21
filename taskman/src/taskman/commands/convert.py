"""`convert` command — relabel a work item's type.

Type tokens (``epic``/``feat``/``task``) are visual labels and convertible.
The only constraint is the ``task = leaf`` invariant: an item that is
currently a directory (has children) cannot become ``task``.
"""
from __future__ import annotations

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import find_item_by_id
from taskman.model.names import (
    WorkItemName,
    WorkItemType,
    emit_name,
    name_is_special_file,
    parse_name,
    special_file_name,
)
from taskman.model.yaml_io import read_file, write_file
from taskman.model.revisions import invalidate_ancestors

_TYPES: tuple[WorkItemType, ...] = ("epic", "feat", "task")


def convert(
    item_id: str = typer.Argument(..., help="Item ID (9 digits)."),
    target_type: str = typer.Argument(..., help="One of: epic, feat, task."),
) -> None:
    """Relabel a work item's type. ``task`` requires no children (leaf invariant)."""
    if target_type not in _TYPES:
        typer.echo(
            f"Error: target type must be one of {list(_TYPES)}, got: {target_type!r}",
            err=True,
        )
        raise typer.Exit(2)

    tasks_dir = get_tasks_dir()
    path = find_item_by_id(tasks_dir, item_id)
    if path is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    wi = parse_name(path.name)
    if wi.status == "done":
        raise typer.BadParameter("closed items are immutable; create a linked follow-up task")
    if wi.item_type == target_type:
        typer.echo(f"No change: {item_id} already typed {target_type}.")
        return

    # Leaf invariant: only file-form items may become task. A directory item,
    # even if currently empty of children, requires explicit collapse first
    # (not in scope for this command).
    if target_type == "task" and path.is_dir():
        children = [
            p for p in path.iterdir() if not name_is_special_file(p.name)
        ]
        if children:
            child_names = ", ".join(sorted(p.name for p in children))
            typer.echo(
                f"Error: cannot convert {item_id} to task — has children: {child_names}",
                err=True,
            )
        else:
            typer.echo(
                f"Error: cannot convert {item_id} to task — item is in directory form. "
                f"Remove the directory representation first.",
                err=True,
            )
        raise typer.Exit(1)

    new_wi = WorkItemName(
        priority=wi.priority,
        item_id=wi.item_id,
        item_type=target_type,  # type: ignore[arg-type]
        status=wi.status,
        slug=wi.slug,
    )
    new_name = emit_name(new_wi, as_directory=path.is_dir())
    new_path = path.parent / new_name
    if new_path.exists() and new_path != path:
        typer.echo(f"Error: target path already exists: {new_path}", err=True)
        raise typer.Exit(1)

    path.rename(new_path)

    body_file = (
        new_path / special_file_name(item_id) if new_path.is_dir() else new_path
    )
    data, body = read_file(body_file)
    data["type"] = target_type
    data["revision"] = int(data.get("revision", 0)) + 1
    write_file(body_file, data, body)
    invalidate_ancestors(tasks_dir, new_path)

    typer.echo(str(new_path))
