"""Sibling order: the store keeps a rank per item.

Children are ordered under their parent. Top-level items are ordered within
their project, under the group key ``root:<PROJECT>``.
"""

from __future__ import annotations

from taskman.storage.store import Store, key_order


def sibling_group(row: dict) -> str:
    """The parent ID, or ``root:<PROJECT>`` for a top-level item."""
    return row["parent_id"] or f"root:{row['metadata'].get('project')}"


def apply_order(rows: list[dict], parents: dict[str, list[str]]) -> list[dict]:
    """Add numeric sibling order without changing work-item metadata."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(sibling_group(row), []).append(row)
    for parent, siblings in groups.items():
        saved = parents.get(parent, [])
        rank = {item_id: index for index, item_id in enumerate(saved)}
        fallback = sorted(
            (row for row in siblings if row["id"] not in rank),
            key=lambda row: key_order(row["id"]),
        )
        complete = saved + [row["id"] for row in fallback]
        complete_rank = {item_id: index for index, item_id in enumerate(complete)}
        for row in siblings:
            row["order"] = complete_rank[row["id"]]
    return rows


def ordered_siblings(store: Store, item_id: str) -> list[dict]:
    """Return the item's siblings, itself included, in their persisted order."""
    from taskman.model.collection import collection

    rows = collection(store)
    by_id = {row["id"]: row for row in rows}
    if item_id not in by_id:
        raise ValueError(f"item not found: {item_id}")
    group = sibling_group(by_id[item_id])
    return sorted(
        (row for row in rows if sibling_group(row) == group),
        key=lambda row: (row["order"], key_order(row["id"])),
    )


def reorder_item(store: Store, item_id: str, position: int) -> list[str]:
    """Move an item to a one-based position among its siblings; a top-level item stays in its project."""
    siblings = ordered_siblings(store, item_id)
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
    store.save_order(sibling_group(moved), order, item_id)
    return order
