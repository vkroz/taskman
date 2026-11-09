"""`search` command — find work items by ID, slug, title, or body.

Ranking (best to worst):
  0  exact ID match
  1  exact slug match
  2  slug contains query
  3  title contains query token (case-insensitive)
  4  body contains query token (case-insensitive)

Lower rank wins; ties broken by ID for stability.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import iter_work_items
from taskman.model.names import NameParseError, parse_name, special_file_name
from taskman.model.yaml_io import FrontmatterError, read_file


@dataclass(frozen=True, slots=True)
class _Match:
    rank: int  # 0 = best
    item_id: str
    item_type: str
    status: str
    priority: str
    slug: str
    title: str
    path: str


def _classify_match(
    query: str, *, item_id: str, slug: str, title: str, body: str
) -> int | None:
    """Return the best rank for this item against the query, or None if no match."""
    q = query.lower()
    if query == item_id:
        return 0
    if q == slug.lower():
        return 1
    if q in slug.lower():
        return 2
    if q in title.lower():
        return 3
    if q in body.lower():
        return 4
    return None


def search(
    query: str = typer.Argument(..., help="Substring to search for."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Search the work-item tree for a query string."""
    tasks_dir = get_tasks_dir()
    matches: list[_Match] = []
    for path in iter_work_items(tasks_dir):
        try:
            wi = parse_name(path.name)
        except NameParseError:
            continue
        body_file = (
            path / special_file_name(wi.item_id) if path.is_dir() else path
        )
        if not body_file.exists():
            continue
        try:
            data, body = read_file(body_file)
        except FrontmatterError:
            continue
        title = str(data.get("title", ""))
        rank = _classify_match(
            query,
            item_id=wi.item_id,
            slug=wi.slug,
            title=title,
            body=body,
        )
        if rank is None:
            continue
        matches.append(
            _Match(
                rank=rank,
                item_id=wi.item_id,
                item_type=wi.item_type,
                status=wi.status,
                priority=wi.priority,
                slug=wi.slug,
                title=title,
                path=str(path),
            )
        )

    matches.sort(key=lambda m: (m.rank, m.item_id))

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "rank": m.rank,
                        "id": m.item_id,
                        "type": m.item_type,
                        "status": m.status,
                        "priority": m.priority,
                        "slug": m.slug,
                        "title": m.title,
                        "path": m.path,
                    }
                    for m in matches
                ],
                indent=2,
            )
        )
        return

    if not matches:
        typer.echo("(no matches)")
        return
    for m in matches:
        typer.echo(
            f"  [{m.rank}] {m.priority} {m.item_type:5} {m.item_id} "
            f"{m.status:7} {m.slug:30} {m.title}"
        )
