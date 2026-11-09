"""Layout helpers: status folders, parent resolution, item enumeration, ID lookup.

The on-disk tree:

    tasks/
      backlog/   <- top-level status folders
      active/
      done/
        <root-item>/                    <- nested item directories
          _<id>.md                      <- special body file (bare ID)
          <child-item>/...
          <child-item>.md               <- leaf-form children

Top-level items live directly under one of the status folders. Nested items
sit inside their parent's directory; the parent's status applies via the
top-level folder, while each nested item's own status lives in its name.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from taskman.model.names import (
    NameParseError,
    WorkItemStatus,
    name_is_special_file,
    parse_name,
    special_file_name,
)

STATUS_FOLDERS: tuple[WorkItemStatus, ...] = ("backlog", "active", "done")


def is_status_folder(name: str) -> bool:
    """True if ``name`` is one of the top-level status folder names."""
    return name in STATUS_FOLDERS


def special_file_path(directory: Path, item_id: str) -> Path:
    """Return the path to the special body file inside a directory item."""
    return directory / special_file_name(item_id)


def is_directory_item(path: Path) -> bool:
    """True if ``path`` on disk is a directory item (has children)."""
    return path.is_dir()


def is_top_level_item(path: Path, tasks_dir: Path) -> bool:
    """True if ``path`` is a top-level work item.

    Top-level means the path sits directly under a status folder, and that
    status folder sits directly under ``tasks_dir``.
    """
    parent = path.parent
    return parent.parent == tasks_dir and is_status_folder(parent.name)


def parent_item_path(child_path: Path, tasks_dir: Path) -> Path | None:
    """Return the parent work-item path, or ``None`` if ``child_path`` is top-level."""
    if is_top_level_item(child_path, tasks_dir):
        return None
    return child_path.parent


def iter_work_items(tasks_dir: Path) -> Iterator[Path]:
    """Yield every work-item path (file or directory) under ``tasks_dir``.

    Skips special body files (``_<id>.md``), top-level status folders, and
    entries whose names don't parse. Each yielded path is either a file
    item (ends in ``.md``, no ``_`` prefix) or a directory item.
    """
    if not tasks_dir.exists():
        return
    for path in tasks_dir.rglob("*"):
        if name_is_special_file(path.name):
            continue
        if path.parent == tasks_dir and is_status_folder(path.name):
            continue
        try:
            parse_name(path.name)
        except NameParseError:
            continue
        yield path


def find_item_by_id(tasks_dir: Path, item_id: str) -> Path | None:
    """Find a work-item path by its ID. Returns ``None`` if not found.

    O(n) scan of the tree. Callers that look up many IDs should cache.
    """
    for path in iter_work_items(tasks_dir):
        try:
            wi = parse_name(path.name)
        except NameParseError:
            continue
        if wi.item_id == item_id:
            return path
    return None
