"""Storage interface for work items.

Commands reach work items only through a :class:`Store`. A backend decides
how items persist; lifecycle rules do not belong to a backend.
"""
from __future__ import annotations

import re

from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

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
    before a write is requested. ``actor`` is the author of the changes:
    the ``--actor`` value, or the git ``user.name`` (decision M1).
    """

    actor: str

    def close(self) -> None:
        """Release the connection."""
        ...

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
        status: str,
        draft: bool,
        project: str | None = None,
    ) -> Item:
        """Store a new item and return it with its allocated key.

        A child belongs to its parent's project; a top-level item needs ``project``.
        """
        ...

    def save(self, item_id: str, metadata: dict[str, Any], body: str) -> Item:
        """Replace an item's metadata and body. Status and type keep their values."""
        ...

    def set_status(self, item_id: str, status: str) -> Item:
        """Change an item's status. Children keep their own status."""
        ...

    def set_type(self, item_id: str, item_type: str) -> Item:
        """Change an item's type label."""
        ...

    def delete(self, item_id: str) -> None:
        """Remove an item that has no children."""
        ...

    def save_order(self, parent_key: str, item_ids: list[str], moved_id: str) -> None:
        """Save the sibling order under a parent ID, or ``root:<PROJECT>``, after ``moved_id`` moved.

        Only the moved item changes position; the other siblings keep their order.
        """
        ...

    def items(self) -> list[Item]:
        """Every work item."""
        ...

    def get(self, item_id: str) -> Item | None:
        """The item with this ID, or ``None``."""
        ...

    def document(self, item_id: str) -> str:
        """The item's full record as Markdown with frontmatter."""
        ...

    def activity(self, item_id: str) -> list[dict[str, Any]]:
        """The item's field changes, oldest first: field, old, new, author and time."""
        ...

    def sibling_order(self) -> dict[str, list[str]]:
        """Saved sibling order: parent ID, or ``root:<PROJECT>`` for top-level items, to ordered IDs."""
        ...

    def projects(self) -> list[dict[str, str]]:
        """Every project as ``key`` and ``name``, oldest first."""
        ...


_TRAILING_NUMBER = re.compile(r"^(.*?)([0-9]+)$")


def key_order(item_id: str) -> tuple[str, int, str]:
    """Sort key for item keys: ``TM-10`` after ``TM-9``."""
    match = _TRAILING_NUMBER.match(item_id)
    return (match[1], int(match[2]), "") if match else (item_id, 0, item_id)


# The store of the running CLI command, so the command and its lock share one connection.
# Holds the store once a command opens it; ``None`` outside a command.
_command_store: ContextVar[list[Store] | None] = ContextVar("taskman_command_store", default=None)


def begin_command() -> Callable[[], None]:
    """Start a command: its first :func:`open_store` opens the store, later calls reuse it.

    The store opens on first use, so ``--help`` needs no database. Returns the
    function that ends the command and closes the store.
    """
    holder: list[Store] = []
    token = _command_store.set(holder)

    def end() -> None:
        _command_store.reset(token)
        for store in holder:
            store.close()

    return end


def open_store() -> Store:
    """The store of the running command, or a new one for the backend that the settings select."""
    holder = _command_store.get()
    if holder:
        return holder[0]
    store = _new_store()
    if holder is not None:
        holder.append(store)
    return store


def _new_store() -> Store:
    from taskman.storage.settings import storage_settings

    from taskman.storage.sql_store import open_sql_store

    return open_sql_store(storage_settings())
