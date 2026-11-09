"""`new` and `finalize` commands.

``new`` creates a work item — top-level or nested under a parent. Nested
creation under a leaf-file parent auto-converts the parent to a directory
holding a special ``_<id>.md`` body file plus the new child.

``finalize`` clears the ``draft: true`` marker on an item after the user
(or agent) has reviewed and approved the auto-generated stub.
"""
from __future__ import annotations

import os
import re
import json
import sys
from pathlib import Path

import typer

from taskman.model.ids import allocate_id, today_prefix
from taskman.model.layout import find_item_by_id, iter_work_items
from taskman.model.names import (
    NameParseError,
    WorkItemName,
    WorkItemStatus,
    WorkItemType,
    emit_name,
    parse_name,
    special_file_name,
)
from taskman.model.yaml_io import read_file, write_file
from taskman.model.revisions import invalidate_ancestors

_SLUGIFY_NON_CHARSET = re.compile(r"[^a-z0-9_]+")
_SLUGIFY_COLLAPSE = re.compile(r"_+")
_VALID_SLUG = re.compile(r"^[a-z0-9_]+$")


class NewCommandError(ValueError):
    """User-visible error from ``new`` or ``finalize``."""


def slugify(title: str) -> str:
    """Derive a slug from a human title.

    Lowercases, replaces any run of non-charset chars with ``_``, collapses
    consecutive underscores, strips leading/trailing ``_``. Raises
    :class:`NewCommandError` if the result is empty or fails validation.
    """
    slug = _SLUGIFY_NON_CHARSET.sub("_", title.lower())
    slug = _SLUGIFY_COLLAPSE.sub("_", slug)
    slug = slug.strip("_")
    if not slug:
        raise NewCommandError(f"could not derive slug from title: {title!r}")
    if not _VALID_SLUG.match(slug):
        raise NewCommandError(f"derived slug invalid: {slug!r}")
    return slug


def get_tasks_dir() -> Path:
    """Resolve the active tasks directory.

    Reads ``TASKMAN_TASKS_DIR`` from the environment if set; otherwise
    falls back to ``$PWD/tasks``.
    """
    env = os.environ.get("TASKMAN_TASKS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    for directory in (Path.cwd(), *Path.cwd().parents):
        if (directory / "tasks").is_dir():
            return directory / "tasks"
        if (directory / ".git").exists():
            return directory / "tasks"
    return Path.cwd() / "tasks"


def _ensure_unique_slug(tasks_dir: Path, slug: str, today: str) -> str:
    """Append ``_2``, ``_3``, … if ``slug`` is already in use today."""
    in_use: set[str] = set()
    for path in iter_work_items(tasks_dir):
        try:
            wi = parse_name(path.name)
        except NameParseError:
            continue
        if wi.item_id.startswith(today):
            in_use.add(wi.slug)
    if slug not in in_use:
        return slug
    n = 2
    while f"{slug}_{n}" in in_use:
        n += 1
    return f"{slug}_{n}"


def _convert_file_to_directory(parent_path: Path, parent_id: str) -> Path:
    """Convert a leaf-file parent into a directory item.

    The original body moves into ``_<id>.md`` inside the new directory and
    the original file is removed. Returns the new directory path.
    """
    if not parent_path.is_file():
        raise NewCommandError(f"parent is not a file: {parent_path}")
    if not parent_path.name.endswith(".md"):
        raise NewCommandError(f"file parent must end with .md: {parent_path.name}")
    dir_path = parent_path.parent / parent_path.name[:-3]
    if dir_path.exists():
        raise NewCommandError(f"cannot convert: directory already exists: {dir_path}")
    body_content = parent_path.read_text(encoding="utf-8")
    dir_path.mkdir()
    (dir_path / special_file_name(parent_id)).write_text(body_content, encoding="utf-8")
    parent_path.unlink()
    return dir_path


def _make_item(
    tasks_dir: Path,
    title: str,
    item_type: WorkItemType,
    parent_id: str | None,
    body: str | None = None,
) -> Path:
    """Core creation logic. Returns the path of the newly created leaf file."""
    item_id = allocate_id(tasks_dir)
    slug = _ensure_unique_slug(tasks_dir, slugify(title), today_prefix())
    status: WorkItemStatus = "backlog"

    if parent_id is None:
        parent_dir = tasks_dir / status
        parent_dir.mkdir(parents=True, exist_ok=True)
    else:
        parent_path = find_item_by_id(tasks_dir, parent_id)
        if parent_path is None:
            raise NewCommandError(f"parent not found: {parent_id}")
        parent_wi = parse_name(parent_path.name)
        if parent_wi.status == "done":
            raise NewCommandError("closed parents are immutable; create a follow-up task")
        if parent_wi.item_type == "task":
            raise NewCommandError(
                f"cannot add child to a task ({parent_id}): task = leaf invariant. "
                f"Convert the parent first with: taskman convert {parent_id} feat"
            )
        parent_dir = (
            _convert_file_to_directory(parent_path, parent_id)
            if parent_path.is_file()
            else parent_path
        )

    wi = WorkItemName(
        priority="00",
        item_id=item_id,
        item_type=item_type,
        status=status,
        slug=slug,
    )
    path = parent_dir / emit_name(wi, as_directory=False)
    if path.exists():
        # Defensive — ID uniqueness should prevent this.
        raise NewCommandError(f"target already exists: {path}")

    data: dict[str, object] = {
        "id": item_id,
        "type": item_type,
        "status": status,
        "priority": "00",
        "slug": slug,
        "title": title,
        "draft": True,
    }
    if body is None:
        body = f"# {title}\n"
    write_file(path, data, body)
    invalidate_ancestors(tasks_dir, path)
    return path


def new(
    title: str = typer.Option(..., "--title", help="Human title."),
    item_type: str = typer.Option("task", "--type", help="One of: epic, feat, task."),
    parent: str | None = typer.Option(
        None,
        "--parent",
        help="Parent item ID (9 digits). Omit for a top-level item.",
    ),
    body_file: str | None = typer.Option(None, "--body-file", help="Markdown body file; - reads stdin."),
    as_json: bool = typer.Option(False, "--json", help="Emit ID and path as JSON."),
) -> None:
    """Create a new work item (draft)."""
    if item_type not in ("epic", "feat", "task"):
        typer.echo(
            f"Error: --type must be one of epic|feat|task, got: {item_type!r}",
            err=True,
        )
        raise typer.Exit(2)
    try:
        path = _make_item(
            tasks_dir=get_tasks_dir(),
            title=title,
            item_type=item_type,  # type: ignore[arg-type]
            parent_id=parent,
            body=(sys.stdin.read() if body_file == "-" else Path(body_file).read_text(encoding="utf-8")) if body_file else None,
        )
    except (NewCommandError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps({"id": parse_name(path.name).item_id, "path": str(path)}) if as_json else str(path))


def finalize(
    item_id: str = typer.Argument(..., help="Item ID to clear the draft marker on."),
) -> None:
    """Clear ``draft: true`` on an item."""
    tasks_dir = get_tasks_dir()
    item_path = find_item_by_id(tasks_dir, item_id)
    if item_path is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    body_file = (
        item_path / special_file_name(item_id) if item_path.is_dir() else item_path
    )
    if not body_file.exists():
        typer.echo(f"Error: body file missing: {body_file}", err=True)
        raise typer.Exit(1)
    data, body = read_file(body_file)
    if data.get("status") == "done":
        typer.echo(f"No change: {item_id} is closed and immutable.")
        return
    if not data.get("draft"):
        typer.echo(f"Item {item_id} is not a draft — nothing to do.")
        return
    data["draft"] = False
    write_file(body_file, data, body)
    typer.echo(f"Finalized: {item_id}")
