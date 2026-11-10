"""Shared context, body updates, dependencies, and evidence for any agent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from taskman import lifecycle
from taskman.commands.outcome import reported
from taskman.storage.store import open_store


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
    body = read_body(body_file)
    with reported():
        item = lifecycle.update_body(open_store(), item_id, body)
    typer.echo(item.location)


def record(
    item_id: str = typer.Argument(...),
    kind: str = typer.Option(..., "--kind", help="decision, progress, handoff, summary, or verification."),
    actor: str = typer.Option(..., "--actor", help="Agent or human responsible for this entry."),
    body_file: str = typer.Option(..., "--body-file", help="Evidence or context; - reads stdin."),
    result: str | None = typer.Option(None, "--result", help="Verification only: pass, fail, or blocked."),
) -> None:
    """Append an attributed journal entry without replacing another agent's context."""
    with reported():
        lifecycle.check_entry(kind, actor, result)
    text = read_body(body_file)
    with reported():
        lifecycle.record(open_store(), item_id, kind, actor, text, result)
    typer.echo(f"Recorded {kind}: {item_id}")


def depend(
    item_id: str = typer.Argument(...),
    on: list[str] | None = typer.Option(None, "--on", help="Dependency ID; repeat to set multiple dependencies."),
    clear: bool = typer.Option(False, "--clear", help="Remove all dependencies."),
) -> None:
    """Replace dependencies, rejecting unknown IDs and cycles before writing."""
    if bool(on) == clear:
        raise typer.BadParameter("supply --on IDs or --clear")
    with reported():
        lifecycle.set_dependencies(open_store(), item_id, on or [])
    typer.echo(f"Updated dependencies: {item_id}")


def discard(item_id: str = typer.Argument(...)) -> None:
    """Remove a draft leaf with no incoming dependencies."""
    with reported():
        lifecycle.discard(open_store(), item_id)
    typer.echo(f"Discarded: {item_id}")


def context(item_id: str = typer.Argument(...)) -> None:
    """Emit task, ancestors, descendants, and dependencies as JSON for a fresh agent."""
    items = {item.id: item for item in open_store().items()}
    if item_id not in items:
        raise typer.BadParameter(f"item not found: {item_id}")

    def payload(item):
        return {"id": item.id, "parent_id": item.parent_id, "path": item.location, "frontmatter": item.metadata, "body": item.body}

    def ancestor_ids(item):
        parent = item.parent_id
        while parent in items:
            yield parent
            parent = items[parent].parent_id

    ancestors = [payload(items[parent]) for parent in ancestor_ids(items[item_id])]
    item = payload(items[item_id])
    descendants = [payload(other) for other in items.values() if item_id in ancestor_ids(other)]
    dependencies = []
    seen = set()
    pending = [d for p in [item, *ancestors, *descendants] for d in p["frontmatter"].get("depends_on", [])]
    while pending:
        dep = str(pending.pop(0))
        if dep in seen:
            continue
        seen.add(dep)
        if dep in items:
            value = payload(items[dep])
            dependencies.append(value)
            pending.extend(value["frontmatter"].get("depends_on", []))
        else:
            dependencies.append({"id": dep, "missing": True})
    typer.echo(json.dumps({"item": item, "ancestors": ancestors, "descendants": descendants, "dependencies": dependencies}, indent=2, default=str))
