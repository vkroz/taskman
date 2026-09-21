"""Shared context, body updates, dependencies, and evidence for any agent."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from taskman.commands.new import get_tasks_dir
from taskman.commands.show import _to_plain
from taskman.model.graph import find_cycles, unknown_references
from taskman.model.layout import find_item_by_id, iter_work_items, parent_item_path
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file, write_file
from taskman.model.revisions import invalidate_ancestors


def load_item(tasks_dir: Path, item_id: str):
    path = find_item_by_id(tasks_dir, item_id)
    if path is None:
        raise typer.BadParameter(f"item not found: {item_id}")
    file = path / special_file_name(item_id) if path.is_dir() else path
    data, body = read_file(file)
    return path, file, data, body


def read_body(source: str) -> str:
    try:
        return sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(str(exc)) from exc


def update(
    item_id: str = typer.Argument(...),
    body_file: str = typer.Option(..., "--body-file", help="Replace Markdown body from file; - for stdin."),
) -> None:
    """Replace the specification/body while preserving managed metadata and journal."""
    path, file, data, _ = load_item(get_tasks_dir(), item_id)
    if data.get("status") == "done":
        raise typer.BadParameter("closed items are immutable; create a linked follow-up task")
    body = read_body(body_file)
    if not body.strip():
        raise typer.BadParameter("body must not be empty")
    data["revision"] = int(data.get("revision", 0)) + 1
    write_file(file, data, body)
    invalidate_ancestors(get_tasks_dir(), path)
    typer.echo(str(path))


def record(
    item_id: str = typer.Argument(...),
    kind: str = typer.Option(..., "--kind", help="decision, progress, handoff, summary, or verification."),
    actor: str = typer.Option(..., "--actor", help="Agent or human responsible for this entry."),
    body_file: str = typer.Option(..., "--body-file", help="Evidence or context; - reads stdin."),
    result: str | None = typer.Option(None, "--result", help="Verification only: pass, fail, or blocked."),
) -> None:
    """Append an attributed journal entry without replacing another agent's context."""
    if kind not in {"decision", "progress", "handoff", "summary", "verification"}:
        raise typer.BadParameter("unknown entry kind")
    if not actor.strip():
        raise typer.BadParameter("actor must not be empty")
    if (kind == "verification" and result not in {"pass", "fail", "blocked"}) or (kind != "verification" and result is not None):
        raise typer.BadParameter("verification requires --result pass|fail|blocked; other entries omit it")
    path, file, data, body = load_item(get_tasks_dir(), item_id)
    if data.get("status") == "done":
        raise typer.BadParameter("closed items are immutable; create a linked follow-up task")
    text = read_body(body_file)
    if not text.strip():
        raise typer.BadParameter("entry must not be empty")
    entries = data.setdefault("journal", [])
    if kind in {"progress", "decision"}:
        data["revision"] = int(data.get("revision", 0)) + 1
    entry = {"kind": kind, "actor": actor, "at": datetime.now(timezone.utc).isoformat(), "text": text.strip(), "revision": int(data.get("revision", 0))}
    if result:
        entry["result"] = result
    entries.append(entry)
    write_file(file, data, body)
    if kind in {"progress", "decision"}:
        invalidate_ancestors(get_tasks_dir(), path)
    typer.echo(f"Recorded {kind}: {item_id}")


def depend(
    item_id: str = typer.Argument(...),
    on: list[str] | None = typer.Option(None, "--on", help="Dependency ID; repeat to set multiple dependencies."),
    clear: bool = typer.Option(False, "--clear", help="Remove all dependencies."),
) -> None:
    """Replace dependencies, rejecting unknown IDs and cycles before writing."""
    if bool(on) == clear:
        raise typer.BadParameter("supply --on IDs or --clear")
    tasks_dir = get_tasks_dir()
    path, file, data, body = load_item(tasks_dir, item_id)
    if data.get("status") == "done":
        raise typer.BadParameter("closed items are immutable; create a linked follow-up task")
    deps = list(dict.fromkeys(on or []))
    graph = {}
    for candidate in iter_work_items(tasks_dir):
        wi = parse_name(candidate.name)
        _, _, meta, _ = load_item(tasks_dir, wi.item_id)
        # A parent completes only after its children. Include those edges
        # when checking cycles, so a child cannot depend on its own parent.
        children = [parse_name(p.name).item_id for p in iter_work_items(tasks_dir) if p.parent == candidate]
        graph[wi.item_id] = list(meta.get("depends_on") or []) + children
    children = [dep for dep in graph[item_id] if dep not in (data.get("depends_on") or [])]
    graph[item_id] = deps + children
    if unknown_references(graph):
        raise typer.BadParameter("dependency ID not found")
    if find_cycles(graph):
        raise typer.BadParameter("dependencies would create a cycle (including parent completion)")
    data["depends_on"] = deps
    data["revision"] = int(data.get("revision", 0)) + 1
    write_file(file, data, body)
    invalidate_ancestors(tasks_dir, path)
    typer.echo(f"Updated dependencies: {item_id}")


def discard(item_id: str = typer.Argument(...)) -> None:
    """Remove a draft leaf with no incoming dependencies."""
    tasks_dir = get_tasks_dir()
    path, _, data, _ = load_item(tasks_dir, item_id)
    if not data.get("draft") or path.is_dir():
        raise typer.BadParameter("only draft leaves can be discarded; handle children first")
    for candidate in iter_work_items(tasks_dir):
        _, _, meta, _ = load_item(tasks_dir, parse_name(candidate.name).item_id)
        if item_id in (meta.get("depends_on") or []):
            raise typer.BadParameter("another item depends on this draft")
    invalidate_ancestors(tasks_dir, path)
    path.unlink()
    typer.echo(f"Discarded: {item_id}")


def context(item_id: str = typer.Argument(...)) -> None:
    """Emit task, ancestors, descendants, and dependencies as JSON for a fresh agent."""
    tasks_dir = get_tasks_dir()
    path, _, _, _ = load_item(tasks_dir, item_id)

    def payload(item_path):
        wi = parse_name(item_path.name)
        _, _, data, body = load_item(tasks_dir, wi.item_id)
        parent = parent_item_path(item_path, tasks_dir)
        return {"id": wi.item_id, "parent_id": parse_name(parent.name).item_id if parent else None, "path": str(item_path), "frontmatter": _to_plain(data), "body": body}

    ancestors = []
    parent = parent_item_path(path, tasks_dir)
    while parent:
        ancestors.append(payload(parent))
        parent = parent_item_path(parent, tasks_dir)
    item = payload(path)
    descendants = [payload(p) for p in iter_work_items(tasks_dir) if p != path and p.is_relative_to(path)]
    dependencies = []
    seen = set()
    pending = [d for p in [item, *ancestors, *descendants] for d in p["frontmatter"].get("depends_on", [])]
    while pending:
        dep = str(pending.pop(0))
        if dep in seen:
            continue
        seen.add(dep)
        target = find_item_by_id(tasks_dir, dep)
        if target:
            value = payload(target)
            dependencies.append(value)
            pending.extend(value["frontmatter"].get("depends_on", []))
        else:
            dependencies.append({"id": dep, "missing": True})
    typer.echo(json.dumps({"item": item, "ancestors": ancestors, "descendants": descendants, "dependencies": dependencies}, indent=2, default=str))
