"""Shared collection queries and non-destructive completed-work consolidation."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from taskman.model.ordering import apply_order
from taskman.storage.store import Store, StorageError, as_store, key_order


def histories(source: Store | Path) -> list[dict]:
    return as_store(source).histories()


def collection(source: Store | Path, include_archived=False) -> list[dict]:
    store = as_store(source)
    archived = {i for h in store.histories() for i in h["ids"]}
    rows = []
    for item in store.items():
        if item.id in archived and not include_archived:
            continue
        if item.error:
            raise ValueError(item.error)
        rows.append(
            {
                "id": item.id,
                "parent_id": item.parent_id,
                "metadata": item.metadata,
                "body": item.body,
                "path": item.location,
                "archived": item.id in archived,
            }
        )
    return apply_order(rows, store.sibling_order())


def field_value(row, key):
    return row.get("parent_id") if key == "parent_id" else row["metadata"].get(key)


def text_value(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def query(
    rows: list[dict], filters=(), keyword="", epic="", sort="priority", descending=False
):
    """Filter matches then order siblings; return matches with contextual ancestors."""
    if not isinstance(filters, (list, tuple)) or not all(
        isinstance(f, dict) and isinstance(f.get("field"), str) for f in filters
    ):
        raise ValueError("Filters must specify a metadata field")
    if not all(isinstance(v, str) for v in (keyword, epic, sort)):
        raise ValueError("Search, epic and sort must be text")
    by_id = {r["id"]: r for r in rows}

    def within(row):
        seen = set()
        while row and row["id"] not in seen:
            if row["id"] == epic:
                return True
            seen.add(row["id"])
            row = by_id.get(row["parent_id"])
        return False

    def matches(row):
        if epic and not within(row):
            return False
        if (
            keyword.casefold()
            not in (text_value(row["metadata"]) + " " + row["body"]).casefold()
        ):
            return False
        for f in filters:
            key, op, value = f["field"], f.get("op", "eq"), str(f.get("value", ""))
            actual = field_value(row, key)
            if op == "exists":
                if (key == "parent_id" or key in row["metadata"]) != (
                    value.lower() != "false"
                ):
                    return False
            elif op == "contains":
                if value.casefold() not in text_value(actual).casefold():
                    return False
            elif op == "eq":
                values = actual if isinstance(actual, list) else [actual]
                if not any(
                    text_value(v).casefold()
                    in [x.strip().casefold() for x in value.split(",")]
                    for v in values
                ):
                    return False
            elif op == "neq":
                values = actual if isinstance(actual, list) else [actual]
                excluded = [x.strip().casefold() for x in value.split(",")]
                if any(text_value(v).casefold() in excluded for v in values):
                    return False
            else:
                raise ValueError("Unknown filter operator")
        return True

    matched = {r["id"] for r in rows if matches(r)}
    visible = set(matched)
    for id in list(matched):
        p = by_id[id]["parent_id"]
        seen = set()
        while p in by_id and p not in seen:
            seen.add(p)
            visible.add(p)
            p = by_id[p]["parent_id"]
    children = {}
    for row in rows:
        if row["id"] in visible:
            parent = row["parent_id"] if row["parent_id"] in visible else None
            children.setdefault(parent, []).append(row)

    def order(row):
        if sort == "priority":
            return (0, row.get("order", 0), key_order(row["id"]))
        value = field_value(row, sort)
        # Numeric priorities and revisions should not sort lexicographically.
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.isdigit()
        ):
            return (0, float(value), key_order(row["id"]))
        return (1, text_value(value).casefold(), key_order(row["id"]))

    result = []

    def walk(parent, depth):
        for row in sorted(children.get(parent, []), key=order, reverse=descending):
            result.append({**row, "context": row["id"] not in matched, "depth": depth})
            walk(row["id"], depth + 1)

    walk(None, 0)
    return result


def compact_text(text: str, limit: int = 420) -> str:
    """Bound each evidence excerpt; the immutable original carries full detail."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " … (full detail in original record)"


def consolidate(source: Store | Path, ids: list[str], title: str) -> dict:
    store = as_store(source)
    if not store.consolidation:
        from taskman.storage.sql_store import NO_CONSOLIDATION

        raise StorageError(NO_CONSOLIDATION)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Enter a summary title of 1–200 characters")
    rows = collection(store, True)
    by_id = {r["id"]: r for r in rows}
    if not ids:
        raise ValueError("Select completed work to consolidate")
    if any(id not in by_id for id in ids):
        raise ValueError("Selected item not found")
    selected = set(ids)
    changed = True
    while changed:
        before = len(selected)
        selected.update(r["id"] for r in rows if r["parent_id"] in selected)
        changed = len(selected) != before
    chosen = [r for r in rows if r["id"] in selected]
    if any(r["metadata"].get("status") != "done" for r in chosen):
        raise ValueError("Every selected item and descendant must be done")
    if any(r["archived"] for r in chosen):
        raise ValueError(
            "Selection includes already consolidated work; select unconsolidated items"
        )
    key = uuid.uuid4().hex
    lines = [
        "# " + title.strip(),
        "",
        "Completed work retained in Taskman; original requirements and evidence remain available by ID.",
        "",
    ]
    for row in chosen:
        meta = row["metadata"]
        lines += [
            "## " + str(meta.get("title", row["id"])),
            "",
            "Taskman ID: " + row["id"],
            "",
        ]
        journal = meta.get("journal") or []
        summaries = [e for e in journal if e.get("kind") == "summary"]
        decisions = [e for e in journal if e.get("kind") == "decision"]
        evidence = [e for e in journal if e.get("kind") == "verification"]
        if not summaries:
            lines += [
                "No completion summary recorded. See original specification below.",
                "",
            ]
        for e in summaries[-1:] + decisions[-3:] + evidence[-1:]:
            lines += [
                f"**{e.get('kind', '').title()}** ({e.get('actor', 'unknown')})"
                + (" — " + str(e["result"]) if "result" in e else ""),
                compact_text(
                    str(e.get("text", "")), 420 if e.get("kind") == "summary" else 220
                ),
                "",
            ]
        if len(decisions) > 3 or len(evidence) > 1:
            lines += [
                f"Original record contains {len(decisions)} decisions and {len(evidence)} verification entries; excerpts above show the latest.",
                "",
            ]
        lines += [f"[Full requirements, decisions and evidence]({store.history_link(row['id'])})", ""]
    return store.save_history(
        {"key": key, "title": title.strip(), "ids": sorted(selected), "summary": "\n".join(lines)}
    )
