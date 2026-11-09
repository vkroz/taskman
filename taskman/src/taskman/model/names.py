"""Name grammar for work items.

On-disk name format:

    <priority>.<type>-<id>.<status>.<slug>          (directory item)
    <priority>.<type>-<id>.<status>.<slug>.md       (file item)

Field separator: dot. Fields:

- ``priority`` — 2-digit numeric. Currently always ``"00"``; reserved for
  future ranking. The parser accepts ``00``-``99``.
- ``type`` — one of ``"epic"`` | ``"feat"`` | ``"task"``. Visual label only;
  convertible between values (see ``convert`` command).
- ``id`` — 9-digit numeric, ``YYMMDDnnn`` (shared per-day counter, gaps
  allowed). Stable across renames and type conversions.
- ``status`` — one of ``"backlog"`` | ``"active"`` | ``"done"``.
- ``slug`` — matches ``[a-z0-9_]+`` — lowercase, digits, underscores only.
  No dots (reserved as field separator), no hyphens (reserved as the
  type/ID separator inside the second field).

A directory item contains a special body file named ``_<id>.md`` — bare
numeric ID, no type prefix — so it survives type conversion without rename.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

WorkItemType = Literal["epic", "feat", "task"]
WorkItemStatus = Literal["backlog", "active", "done"]

_PRIORITY_RE = re.compile(r"^[0-9]{2}$")
_ID_RE = re.compile(r"^[0-9]{9}$")
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_TYPES: tuple[WorkItemType, ...] = ("epic", "feat", "task")
_STATUSES: tuple[WorkItemStatus, ...] = ("backlog", "active", "done")

_MD_SUFFIX = ".md"
_SPECIAL_PREFIX = "_"


class NameParseError(ValueError):
    """Raised when a name does not match the work-item grammar."""


@dataclass(frozen=True, slots=True)
class WorkItemName:
    """Structured form of a work item's on-disk identity tokens."""

    priority: str
    item_id: str
    item_type: WorkItemType
    status: WorkItemStatus
    slug: str

    def __post_init__(self) -> None:
        if not _PRIORITY_RE.match(self.priority):
            raise NameParseError(f"priority must be 2 digits, got: {self.priority!r}")
        if not _ID_RE.match(self.item_id):
            raise NameParseError(f"item_id must be 9 digits, got: {self.item_id!r}")
        if self.item_type not in _TYPES:
            raise NameParseError(
                f"item_type must be one of {_TYPES}, got: {self.item_type!r}"
            )
        if self.status not in _STATUSES:
            raise NameParseError(
                f"status must be one of {_STATUSES}, got: {self.status!r}"
            )
        if not _SLUG_RE.match(self.slug):
            raise NameParseError(
                f"slug must match {_SLUG_RE.pattern}, got: {self.slug!r}"
            )


def parse_name(name: str) -> WorkItemName:
    """Decode an on-disk name into its structured form.

    Accepts either a file name (ending in ``.md``) or a directory name
    (no extension). The two forms produce identical ``WorkItemName`` data;
    callers that care about representation should check the path on disk
    or use :func:`emit_name` with the appropriate ``as_directory`` flag.

    Raises :class:`NameParseError` on any grammar deviation.
    """
    stem = name[: -len(_MD_SUFFIX)] if name.endswith(_MD_SUFFIX) else name
    parts = stem.split(".")
    if len(parts) != 4:
        raise NameParseError(
            f"expected 4 dot-separated fields, got {len(parts)} in: {name!r}"
        )
    priority, typed_id, status, slug = parts
    if "-" not in typed_id:
        raise NameParseError(
            f"second field must be '<type>-<id>', got: {typed_id!r}"
        )
    item_type, _, item_id = typed_id.partition("-")
    return WorkItemName(
        priority=priority,
        item_id=item_id,
        item_type=item_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        slug=slug,
    )


def emit_name(wi: WorkItemName, *, as_directory: bool) -> str:
    """Render a :class:`WorkItemName` as its on-disk name string."""
    base = f"{wi.priority}.{wi.item_type}-{wi.item_id}.{wi.status}.{wi.slug}"
    return base if as_directory else base + _MD_SUFFIX


def name_is_special_file(name: str) -> bool:
    """True if ``name`` is a directory item's special body file (``_<id>.md``)."""
    return name.startswith(_SPECIAL_PREFIX) and name.endswith(_MD_SUFFIX)


def special_file_name(item_id: str) -> str:
    """Return the special-file basename for a directory item with this ID.

    Uses the bare numeric ID (no type prefix), so the file does not need
    renaming when the item's type label changes.
    """
    if not _ID_RE.match(item_id):
        raise NameParseError(f"item_id must be 9 digits, got: {item_id!r}")
    return f"{_SPECIAL_PREFIX}{item_id}{_MD_SUFFIX}"
