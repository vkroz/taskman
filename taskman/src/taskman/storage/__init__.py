"""Storage interface for work items.

Commands reach work items only through a :class:`Store`. A backend decides
how items persist; lifecycle rules do not belong to a backend.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

BACKENDS = ("file",)


class StorageConfigError(ValueError):
    """The configured storage backend is not supported."""


@dataclass(frozen=True, slots=True)
class Item:
    """One work item as every backend reports it.

    ``metadata`` holds the managed fields and journal as plain data.
    ``location`` is where the backend keeps the item (a path for the file
    backend). ``error`` is set when the item exists but its record cannot be
    read; ``metadata`` and ``body`` are then empty.
    """

    id: str
    type: str
    status: str
    priority: str
    slug: str
    parent_id: str | None
    metadata: dict[str, Any]
    body: str
    location: str
    error: str | None = None

    @property
    def title(self) -> str:
        return str(self.metadata.get("title", ""))

    @property
    def depends_on(self) -> tuple[str, ...]:
        raw = self.metadata.get("depends_on") or []
        return tuple(str(d) for d in raw) if isinstance(raw, list) else ()

    @property
    def draft(self) -> bool:
        return bool(self.metadata.get("draft"))


class Store(Protocol):
    """Read operations every backend provides."""

    def items(self) -> list[Item]:
        """Every work item, including consolidated ones."""

    def get(self, item_id: str) -> Item | None:
        """The item with this ID, or ``None``."""

    def document(self, item_id: str) -> str:
        """The item's full record as Markdown with frontmatter."""

    def histories(self) -> list[dict]:
        """Consolidation records: key, title, summary, path and original IDs."""

    def sibling_order(self) -> dict[str, list[str]]:
        """Saved sibling order: parent ID (or ``root``) to ordered child IDs."""


def backend_name() -> str:
    """The backend named by ``TASKMAN_BACKEND``. Defaults to ``file``."""
    name = os.environ.get("TASKMAN_BACKEND", "").strip() or "file"
    if name not in BACKENDS:
        raise StorageConfigError(
            f"Unknown storage backend {name!r} in TASKMAN_BACKEND. Supported: {', '.join(BACKENDS)}."
        )
    return name


def open_store() -> Store:
    """Open the configured backend."""
    backend_name()
    from taskman.storage.file import FileStore, get_tasks_dir

    return FileStore(get_tasks_dir())


def as_store(source: Store | Path | str) -> Store:
    """Accept a store, or a tasks directory from callers not yet on the interface."""
    if isinstance(source, (Path, str)):
        from taskman.storage.file import FileStore

        return FileStore(Path(source))
    return source
