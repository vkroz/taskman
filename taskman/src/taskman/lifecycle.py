"""Lifecycle rules for work items, independent of the storage backend.

Every mutation checks its rules here, then asks the store to persist the
result. Errors carry the user-facing message; ``usage`` marks errors that
come from the caller's arguments (the CLI reports those with exit code 2).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taskman.model.graph import find_cycles, unknown_references
from taskman.storage.store import Item, Store

_SLUGIFY_NON_CHARSET = re.compile(r"[^a-z0-9_]+")
_SLUGIFY_COLLAPSE = re.compile(r"_+")
_VALID_SLUG = re.compile(r"^[a-z0-9_]+$")

IMMUTABLE = "closed items are immutable; create a linked follow-up task"
JOURNAL_KINDS = ("decision", "progress", "handoff", "summary", "verification")
RESULTS = ("pass", "fail", "blocked")


class LifecycleError(ValueError):
    """A rule rejected the change."""

    def __init__(self, message: str, *, usage: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.usage = usage


class NoChange(Exception):
    """The item already has the requested state."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def slugify(title: str) -> str:
    """Derive a slug from a human title.

    Lowercases, replaces any run of non-charset chars with ``_``, collapses
    consecutive underscores, strips leading/trailing ``_``. Raises
    :class:`LifecycleError` if the result is empty or fails validation.
    """
    slug = _SLUGIFY_NON_CHARSET.sub("_", title.lower())
    slug = _SLUGIFY_COLLAPSE.sub("_", slug)
    slug = slug.strip("_")
    if not slug:
        raise LifecycleError(f"could not derive slug from title: {title!r}")
    if not _VALID_SLUG.match(slug):
        raise LifecycleError(f"derived slug invalid: {slug!r}")
    return slug


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _revision(item: Item) -> int:
    return int(item.metadata.get("revision", 0))


def _require(store: Store, item_id: str, *, usage: bool) -> Item:
    item = store.get(item_id)
    if item is None:
        raise LifecycleError(f"item not found: {item_id}", usage=usage)
    return item


def _readable(item: Item, *, usage: bool) -> Item:
    if item.error:
        raise LifecycleError(item.error, usage=usage)
    return item


def _open(item: Item) -> None:
    if item.metadata.get("status") == "done":
        raise LifecycleError(IMMUTABLE, usage=True)


def _ancestors(items: dict[str, Item], item: Item) -> list[Item]:
    chain = []
    parent = item.parent_id
    while parent in items:
        chain.append(items[parent])
        parent = items[parent].parent_id
    return chain


def _descendants(items: dict[str, Item], item_id: str) -> list[Item]:
    """Descendants in the store's item order."""
    children: dict[str, list[str]] = {}
    for other in items.values():
        if other.parent_id:
            children.setdefault(other.parent_id, []).append(other.id)
    found: set[str] = set()
    pending = list(children.get(item_id, []))
    while pending:
        child = pending.pop()
        found.add(child)
        pending.extend(children.get(child, []))
    return [other for other in items.values() if other.id in found]


def is_ready(item: Item, items: dict[str, Item]) -> bool:
    """True when the item and every ancestor are finalized, open, and unblocked."""
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


def invalidate_ancestors(store: Store, item_id: str) -> None:
    """Bump each ancestor's revision: its acceptance evidence no longer covers its children."""
    parent_id = store.get(item_id).parent_id
    while parent_id:
        ancestor = store.get(parent_id)
        metadata = copy.deepcopy(ancestor.metadata)
        metadata["revision"] = _revision(ancestor) + 1
        store.save(ancestor.id, metadata, ancestor.body)
        parent_id = ancestor.parent_id


def create(store: Store, title: str, item_type: str, parent_id: str | None, body: str | None = None) -> Item:
    slug = slugify(title)
    if parent_id is not None:
        parent = store.get(parent_id)
        if parent is None:
            raise LifecycleError(f"parent not found: {parent_id}")
        if parent.status == "done":
            raise LifecycleError("closed parents are immutable; create a follow-up task")
        if parent.type == "task":
            raise LifecycleError(
                f"cannot add child to a task ({parent_id}): task = leaf invariant. "
                f"Convert the parent first with: taskman convert {parent_id} feat"
            )
    item = store.create(
        item_type=item_type,
        title=title,
        body=f"# {title}\n" if body is None else body,
        parent_id=parent_id,
        slug=slug,
        status="backlog",
        draft=True,
    )
    invalidate_ancestors(store, item.id)
    return item


def finalize(store: Store, item_id: str) -> Item:
    item = _readable(_require(store, item_id, usage=False), usage=False)
    if item.metadata.get("status") == "done":
        raise NoChange(f"No change: {item_id} is closed and immutable.")
    if not item.draft:
        raise NoChange(f"Item {item_id} is not a draft — nothing to do.")
    metadata = copy.deepcopy(item.metadata)
    metadata["draft"] = False
    return store.save(item_id, metadata, item.body)


def update_body(store: Store, item_id: str, body: str) -> Item:
    item = _require(store, item_id, usage=True)
    _open(item)
    if not body.strip():
        raise LifecycleError("body must not be empty", usage=True)
    metadata = copy.deepcopy(item.metadata)
    metadata["revision"] = _revision(item) + 1
    item = store.save(item_id, metadata, body)
    invalidate_ancestors(store, item_id)
    return item


def check_entry(kind: str, actor: str, result: str | None) -> None:
    """Validate journal entry arguments before any item is read."""
    if kind not in JOURNAL_KINDS:
        raise LifecycleError("unknown entry kind", usage=True)
    if not actor.strip():
        raise LifecycleError("actor must not be empty", usage=True)
    if (kind == "verification" and result not in RESULTS) or (kind != "verification" and result is not None):
        raise LifecycleError("verification requires --result pass|fail|blocked; other entries omit it", usage=True)


def record(store: Store, item_id: str, kind: str, actor: str, text: str, result: str | None = None) -> Item:
    check_entry(kind, actor, result)
    item = _require(store, item_id, usage=True)
    _open(item)
    if not text.strip():
        raise LifecycleError("entry must not be empty", usage=True)
    metadata = copy.deepcopy(item.metadata)
    revises = kind in {"progress", "decision"}
    if revises:
        metadata["revision"] = _revision(item) + 1
    entry: dict[str, Any] = {
        "kind": kind,
        "actor": actor,
        "at": _now(),
        "text": text.strip(),
        "revision": int(metadata.get("revision", 0)),
    }
    if result:
        entry["result"] = result
    metadata.setdefault("journal", []).append(entry)
    item = store.save(item_id, metadata, item.body)
    if revises:
        invalidate_ancestors(store, item_id)
    return item


def set_dependencies(store: Store, item_id: str, depends_on: list[str]) -> Item:
    item = _require(store, item_id, usage=True)
    _open(item)
    deps = list(dict.fromkeys(depends_on))
    items = {other.id: other for other in store.items()}
    # A parent completes only after its children. Include those edges when
    # checking cycles, so a child cannot depend on its own parent.
    children: dict[str, list[str]] = {}
    for other in items.values():
        if other.parent_id:
            children.setdefault(other.parent_id, []).append(other.id)
    graph = {other_id: list(other.depends_on) + children.get(other_id, []) for other_id, other in items.items()}
    graph[item_id] = deps + children.get(item_id, [])
    if unknown_references(graph):
        raise LifecycleError("dependency ID not found", usage=True)
    if find_cycles(graph):
        raise LifecycleError("dependencies would create a cycle (including parent completion)", usage=True)
    metadata = copy.deepcopy(item.metadata)
    metadata["depends_on"] = deps
    metadata["revision"] = _revision(item) + 1
    item = store.save(item_id, metadata, item.body)
    invalidate_ancestors(store, item_id)
    return item


def discard(store: Store, item_id: str) -> None:
    items = {item.id: item for item in store.items()}
    if item_id not in items:
        raise LifecycleError(f"item not found: {item_id}", usage=True)
    item = items[item_id]
    has_children = any(other.parent_id == item_id for other in items.values())
    if item.metadata.get("status") == "done" or not item.draft or has_children:
        raise LifecycleError("only draft leaves can be discarded; handle children first", usage=True)
    if any(item_id in other.depends_on for other in items.values()):
        raise LifecycleError("another item depends on this draft", usage=True)
    invalidate_ancestors(store, item_id)
    store.delete(item_id)


def move(store: Store, item_id: str, target_status: str) -> Item:
    item = _require(store, item_id, usage=False)
    if target_status == "done":
        return close(store, item_id)
    if item.status == "done":
        raise LifecycleError(IMMUTABLE, usage=True)
    if item.status == target_status:
        raise NoChange(f"No change: {item_id} already in {target_status}.")
    items = {other.id: other for other in store.items()}
    if any(ancestor.status == "done" for ancestor in _ancestors(items, item)):
        raise LifecycleError("closed ancestors are immutable; create a follow-up task", usage=True)
    if target_status == "active":
        for current in [item, *_ancestors(items, item)]:
            if current.draft:
                raise LifecycleError("finalize the item and its ancestors before starting work", usage=True)
            for dep in current.depends_on:
                other = _require(store, dep, usage=True)
                if other.metadata.get("status") != "done":
                    raise LifecycleError(f"dependency is not done: {dep}", usage=True)
    moved = store.set_status(item_id, target_status)
    invalidate_ancestors(store, item_id)
    return moved


def convert(store: Store, item_id: str, target_type: str) -> Item:
    item = _require(store, item_id, usage=False)
    if item.status == "done":
        raise LifecycleError(IMMUTABLE, usage=True)
    if item.type == target_type:
        raise NoChange(f"No change: {item_id} already typed {target_type}.")
    if target_type == "task":
        children = [other for other in store.items() if other.parent_id == item_id]
        if children:
            names = ", ".join(sorted(Path(child.location).name for child in children))
            raise LifecycleError(f"cannot convert {item_id} to task — has children: {names}")
    store.set_type(item_id, target_type)
    item = store.get(item_id)
    metadata = copy.deepcopy(item.metadata)
    metadata["revision"] = _revision(item) + 1
    item = store.save(item_id, metadata, item.body)
    invalidate_ancestors(store, item_id)
    return item


def close(store: Store, item_id: str) -> Item:
    item = _require(store, item_id, usage=False)
    if item.status == "done":
        raise NoChange(f"No change: {item_id} already done.")
    items = {other.id: other for other in store.items()}
    unfinished = [other for other in _descendants(items, item_id) if other.status != "done"]
    if unfinished:
        lines = [f"cannot close {item_id} — descendants not in done:"]
        lines += [f"  {Path(other.location).name}" for other in unfinished]
        raise LifecycleError("\n".join(lines))
    _readable(item, usage=False)
    if item.draft:
        raise LifecycleError("finalize the draft before closing it", usage=True)
    readable = {other_id: other for other_id, other in items.items() if not other.error}
    if not is_ready(readable[item_id], readable):
        raise LifecycleError("item or ancestor has an unfinished dependency or is a draft", usage=True)
    entries = item.metadata.get("journal") or []
    revision = _revision(item)
    checks = [e for e in entries if e.get("kind") == "verification"]
    if not checks or checks[-1].get("result") != "pass" or checks[-1].get("revision") != revision:
        raise LifecycleError("record a passing verification for the current revision before closing", usage=True)
    if not any(e.get("kind") == "summary" and e.get("revision") == revision for e in entries):
        raise LifecycleError("record a summary for the current revision before closing", usage=True)
    closed = store.set_status(item_id, "done")
    invalidate_ancestors(store, item_id)
    return closed


def edit(store: Store, item_id: str, title: Any, body: Any, reason: Any) -> Item:
    item = _require(store, item_id, usage=True)
    if item.metadata.get("status") == "done":
        raise LifecycleError("Completed work is immutable; create a follow-up task", usage=True)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise LifecycleError("Enter a title of 1–200 characters", usage=True)
    if not isinstance(body, str) or not body.strip():
        raise LifecycleError("Description must not be empty", usage=True)
    if not isinstance(reason, str) or not reason.strip():
        raise LifecycleError("Explain why the task changed", usage=True)
    metadata = copy.deepcopy(item.metadata)
    metadata["title"] = title.strip()
    metadata["revision"] = _revision(item) + 1
    metadata.setdefault("journal", []).append(
        {
            "kind": "decision",
            "actor": "Taskman user",
            "at": _now(),
            "text": reason.strip(),
            "revision": metadata["revision"],
        }
    )
    item = store.save(item_id, metadata, body)
    invalidate_ancestors(store, item_id)
    return item
