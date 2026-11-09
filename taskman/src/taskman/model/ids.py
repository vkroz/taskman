"""ID allocator for work items.

Format: ``YYMMDDnnn`` — 6-digit date prefix + 3-digit per-day sequence shared
across all types. Gaps are allowed: allocation returns ``max_seq_today + 1``,
not the lowest unused value. Once 999 is allocated for a given day, the next
allocation raises :class:`RuntimeError`.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from taskman.model.names import NameParseError, parse_name


def today_prefix(today: date | None = None) -> str:
    """Return the ``YYMMDD`` prefix for today (or the given date)."""
    d = today if today is not None else date.today()
    return d.strftime("%y%m%d")


def allocate_id(tasks_dir: Path, *, today: date | None = None) -> str:
    """Allocate the next ID by scanning the tree for the max sequence used today.

    Returns a 9-digit ``YYMMDDnnn`` string. Scans all files and directories
    under ``tasks_dir``; entries whose names do not parse are silently
    skipped. Raises :class:`RuntimeError` if today's 999 counter is exhausted.
    """
    prefix = today_prefix(today)
    max_seq = -1
    if tasks_dir.exists():
        for path in tasks_dir.rglob("*"):
            try:
                wi = parse_name(path.name)
            except NameParseError:
                continue
            if wi.item_id.startswith(prefix):
                seq = int(wi.item_id[6:])
                if seq > max_seq:
                    max_seq = seq
    next_seq = max_seq + 1
    if next_seq > 999:
        raise RuntimeError(
            f"daily ID counter exhausted for prefix {prefix} (999 already used)"
        )
    return f"{prefix}{next_seq:03d}"
