"""Shared collection queries and non-destructive completed-work consolidation."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from taskman.model.layout import iter_work_items, parent_item_path
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file
from taskman.commands.show import _to_plain


def history_dir(tasks_dir: Path) -> Path:
    return tasks_dir.parent / "docs" / "taskman-history"


def histories(tasks_dir: Path) -> list[dict]:
    records = []
    for path in sorted(history_dir(tasks_dir).glob("*.json")):
        try:
            value = json.loads(path.read_text())
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("ids"), list)
                or not all(isinstance(i, str) for i in value["ids"])
            ):
                continue
            if not all(
                isinstance(value.get(k), str)
                for k in ("key", "title", "summary", "path")
            ):
                continue
            # Never hide records if the published documentation has disappeared.
            if not (history_dir(tasks_dir) / (value["key"] + ".md")).is_file():
                continue
            records.append(value)
        except (OSError, ValueError, TypeError):
            continue
    return records


def collection(tasks_dir: Path, include_archived=False) -> list[dict]:
    archived = {i for h in histories(tasks_dir) for i in h["ids"]}
    rows = []
    for path in iter_work_items(tasks_dir):
        wi = parse_name(path.name)
        if wi.item_id in archived and not include_archived:
            continue
        file = path / special_file_name(wi.item_id) if path.is_dir() else path
        data, body = read_file(file)
        parent = parent_item_path(path, tasks_dir)
        rows.append(
            {
                "id": wi.item_id,
                "parent_id": parse_name(parent.name).item_id if parent else None,
                "metadata": _to_plain(data),
                "body": body,
                "path": str(path),
                "archived": wi.item_id in archived,
            }
        )
    return rows


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
        value = field_value(row, sort)
        # Numeric priorities and revisions should not sort lexicographically.
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.isdigit()
        ):
            return (0, float(value), row["id"])
        return (1, text_value(value).casefold(), row["id"])

    result = []

    def walk(parent, depth):
        for row in sorted(children.get(parent, []), key=order, reverse=descending):
            result.append({**row, "context": row["id"] not in matched, "depth": depth})
            walk(row["id"], depth + 1)

    walk(None, 0)
    return result


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def compact_text(text: str, limit: int = 420) -> str:
    """Bound each evidence excerpt; the immutable original carries full detail."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " … (full detail in original record)"


def consolidate(tasks_dir: Path, ids: list[str], title: str) -> dict:
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Enter a summary title of 1–200 characters")
    rows = collection(tasks_dir, True)
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
    directory = history_dir(tasks_dir)
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
        original = Path(row["path"])
        if original.is_dir():
            original = original / special_file_name(row["id"])
        relative = os.path.relpath(original, directory)
        lines += [f"[Full requirements, decisions and evidence]({relative})", ""]
    summary = "\n".join(lines)
    result = {
        "key": key,
        "title": title.strip(),
        "ids": sorted(selected),
        "summary": summary,
        "path": str(Path("docs") / "taskman-history" / f"{key}.md"),
    }
    atomic_text(directory / f"{key}.md", summary)
    atomic_text(directory / f"{key}.json", json.dumps(result, indent=2))
    return result
