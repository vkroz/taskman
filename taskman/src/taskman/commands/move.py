"""Change item status, preserving nesting and enforcing completion gates."""
from __future__ import annotations

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import find_item_by_id, is_top_level_item
from taskman.model.names import (
    WorkItemName,
    WorkItemStatus,
    emit_name,
    parse_name,
    special_file_name,
)
from taskman.model.yaml_io import read_file, write_file
from taskman.model.revisions import invalidate_ancestors

_TARGETS: tuple[WorkItemStatus, ...] = ("backlog", "active", "done")


class MoveCommandError(ValueError):
    """User-visible error from ``move``."""


def move(
    item_id: str = typer.Argument(..., help="Item ID (9 digits)."),
    target_status: str = typer.Argument(..., help="One of: backlog, active, done."),
) -> None:
    """Change an item's status; nested items stay inside their parent."""
    if target_status not in _TARGETS:
        typer.echo(
            f"Error: target status must be one of {list(_TARGETS)}, got: {target_status!r}",
            err=True,
        )
        raise typer.Exit(2)

    tasks_dir = get_tasks_dir()
    path = find_item_by_id(tasks_dir, item_id)
    if path is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    if target_status == "done":
        from taskman.commands.close import close
        close(item_id)
        return

    wi = parse_name(path.name)
    if wi.status == "done" and target_status != "done":
        raise typer.BadParameter("closed items are immutable; create a linked follow-up task")
    if wi.status == target_status:
        typer.echo(f"No change: {item_id} already in {target_status}.")
        return

    from taskman.commands.context import load_item
    from taskman.model.layout import parent_item_path
    _, _, data, _ = load_item(tasks_dir, item_id)
    ancestor = parent_item_path(path, tasks_dir)
    while ancestor:
        if parse_name(ancestor.name).status == "done":
            raise typer.BadParameter("closed ancestors are immutable; create a follow-up task")
        ancestor = parent_item_path(ancestor, tasks_dir)
    if target_status == "active":
        current = path
        while current:
            _, _, meta, _ = load_item(tasks_dir, parse_name(current.name).item_id)
            if meta.get("draft"):
                raise typer.BadParameter("finalize the item and its ancestors before starting work")
            for dep in meta.get("depends_on") or []:
                _, _, other, _ = load_item(tasks_dir, str(dep))
                if other.get("status") != "done":
                    raise typer.BadParameter(f"dependency is not done: {dep}")
            current = parent_item_path(current, tasks_dir)

    is_dir = path.is_dir()
    new_wi = WorkItemName(
        priority=wi.priority,
        item_id=wi.item_id,
        item_type=wi.item_type,
        status=target_status,  # type: ignore[arg-type]
        slug=wi.slug,
    )
    new_name = emit_name(new_wi, as_directory=is_dir)
    target_dir = tasks_dir / target_status if is_top_level_item(path, tasks_dir) else path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    new_path = target_dir / new_name

    if new_path.exists():
        typer.echo(f"Error: target path already exists: {new_path}", err=True)
        raise typer.Exit(1)

    # Atomic rename (single syscall on POSIX) moves the file or whole dir tree.
    path.rename(new_path)

    # Update YAML status to match the new in-name token.
    body_file = (
        new_path / special_file_name(item_id) if new_path.is_dir() else new_path
    )
    data, body = read_file(body_file)
    data["status"] = target_status
    write_file(body_file, data, body)
    invalidate_ancestors(tasks_dir, new_path)

    typer.echo(str(new_path))
