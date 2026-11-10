"""Storage interface for work items.

Commands reach work items only through a :class:`Store`. A backend decides
how items persist; lifecycle rules do not belong to a backend.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from taskman.integrity import Finding

class StorageConfigError(ValueError):
    """The configured storage backend is not supported."""


class StorageError(ValueError):
    """A backend could not persist a change, for example a name collision."""


@dataclass(frozen=True, slots=True)
class Item:
    """One work item as every backend reports it.

    ``metadata`` holds the managed fields and journal as plain data.
    ``location`` is where the backend keeps the item (a path for the file
    backend). ``error`` is set when the item exists but its record cannot be
    read; ``metadata`` and ``body`` are then empty. ``modified`` is the POSIX
    time of the last change to the record.
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
    modified: float = 0.0

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
    """Operations every backend provides.

    Write operations persist what they are given. Lifecycle rules (drafts,
    close gates, immutability, revisions) are checked by ``taskman.lifecycle``
    before a write is requested.
    """

    def lock(self) -> AbstractContextManager[None]:
        """Serialize mutations of this store across processes."""
        ...

    def create(
        self,
        *,
        item_type: str,
        title: str,
        body: str,
        parent_id: str | None,
        slug: str,
        status: str,
        draft: bool,
    ) -> Item:
        """Store a new item and return it with its allocated ID."""
        ...

    def save(self, item_id: str, metadata: dict[str, Any], body: str) -> Item:
        """Replace an item's metadata and body. Status and type keep their values."""
        ...

    def set_status(self, item_id: str, status: str) -> Item:
        """Change an item's status. A top-level item takes its subtree along."""
        ...

    def set_type(self, item_id: str, item_type: str) -> Item:
        """Change an item's type label."""
        ...

    def delete(self, item_id: str) -> None:
        """Remove an item that has no children."""
        ...

    def save_order(self, parent_key: str, item_ids: list[str]) -> None:
        """Save the sibling order under a parent ID, or ``root``."""
        ...

    def check_layout(self) -> list[Finding]:
        """Integrity findings specific to how this backend stores items."""
        ...

    def history_link(self, item_id: str) -> str:
        """A link from a consolidation summary to the item's full record."""
        ...

    def save_history(self, record: dict[str, Any]) -> dict[str, Any]:
        """Store a consolidation record and return it with its ``path``."""
        ...

    def items(self) -> list[Item]:
        """Every work item, including consolidated ones."""
        ...

    def get(self, item_id: str) -> Item | None:
        """The item with this ID, or ``None``."""
        ...

    def document(self, item_id: str) -> str:
        """The item's full record as Markdown with frontmatter."""
        ...

    def histories(self) -> list[dict]:
        """Consolidation records: key, title, summary, path and original IDs."""
        ...

    def sibling_order(self) -> dict[str, list[str]]:
        """Saved sibling order: parent ID (or ``root``) to ordered child IDs."""
        ...


def open_store() -> Store:
    """Open the backend that the storage settings select."""
    from taskman.storage.settings import storage_settings

    settings = storage_settings()
    if settings.backend != "file":
        raise StorageConfigError(
            f"The {settings.backend} backend is not available yet. Unset TASKMAN_BACKEND or set it to file."
        )
    from taskman.storage.file import FileStore, get_tasks_dir

    return FileStore(get_tasks_dir())


def as_store(source: Store | Path | str) -> Store:
    """Accept a store, or a tasks directory from callers not yet on the interface."""
    if isinstance(source, (Path, str)):
        from taskman.storage.file import FileStore

        return FileStore(Path(source))
    return source
