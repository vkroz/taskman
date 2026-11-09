"""Durable sibling ordering stored outside immutable work-item records."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


_ORDER_FILE = ".taskman-order.json"
_ROOT = "root"


def _read(tasks_dir: Path) -> dict[str, list[str]]:
    path = tasks_dir / _ORDER_FILE
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read persisted task order: {exc}") from exc
    parents = value.get("parents") if isinstance(value, dict) else None
    if not isinstance(parents, dict) or not all(
        isinstance(key, str)
        and isinstance(ids, list)
        and all(isinstance(item_id, str) for item_id in ids)
        for key, ids in parents.items()
    ):
        raise ValueError("Persisted task order is invalid")
    return parents


def _write(tasks_dir: Path, parents: dict[str, list[str]]) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / _ORDER_FILE
    handle, temporary = tempfile.mkstemp(prefix=".taskman-order-", dir=tasks_dir)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump({"version": 1, "parents": parents}, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_order(rows: list[dict], tasks_dir: Path) -> list[dict]:
    """Add numeric sibling order without changing work-item metadata."""
    parents = _read(tasks_dir)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["parent_id"] or _ROOT, []).append(row)
    for parent, siblings in groups.items():
        saved = parents.get(parent, [])
        rank = {item_id: index for index, item_id in enumerate(saved)}
        fallback = sorted(
            (row for row in siblings if row["id"] not in rank),
            key=lambda row: (str(row["metadata"].get("priority", "")), row["id"]),
        )
        complete = saved + [row["id"] for row in fallback]
        complete_rank = {item_id: index for index, item_id in enumerate(complete)}
        for row in siblings:
            row["order"] = complete_rank[row["id"]]
    return rows


def ordered_siblings(tasks_dir: Path, item_id: str) -> list[dict]:
    """Return ordinary siblings in their persisted order."""
    from taskman.model.collection import collection

    rows = collection(tasks_dir)
    by_id = {row["id"]: row for row in rows}
    if item_id not in by_id:
        raise ValueError(f"item not found: {item_id}")
    parent_id = by_id[item_id]["parent_id"]
    return sorted(
        (row for row in rows if row["parent_id"] == parent_id),
        key=lambda row: (row["order"], row["id"]),
    )


def reorder_item(tasks_dir: Path, item_id: str, position: int) -> list[str]:
    """Move an item to a one-based sibling position and atomically persist it."""
    siblings = ordered_siblings(tasks_dir, item_id)
    if not isinstance(position, int) or isinstance(position, bool):
        raise ValueError("Position must be an integer")
    if position < 1 or position > len(siblings):
        raise ValueError(f"Position must be between 1 and {len(siblings)}")
    current = next(index for index, row in enumerate(siblings) if row["id"] == item_id)
    target = position - 1
    if current == target:
        return [row["id"] for row in siblings]
    moved = siblings.pop(current)
    siblings.insert(target, moved)
    order = [row["id"] for row in siblings]
    parents = _read(tasks_dir)
    parents[moved["parent_id"] or _ROOT] = order
    _write(tasks_dir, parents)
    return order
