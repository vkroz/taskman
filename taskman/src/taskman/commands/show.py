"""`show` command — print a work item's frontmatter and body."""
from __future__ import annotations

import json
from typing import Any

import typer

from taskman.commands.new import get_tasks_dir
from taskman.model.layout import find_item_by_id
from taskman.model.names import special_file_name
from taskman.model.yaml_io import read_file


def _to_plain(obj: Any) -> Any:
    """Recursively convert ruamel.yaml containers to plain dict/list/scalar."""
    if hasattr(obj, "items") and callable(obj.items):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


def show(
    item_id: str = typer.Argument(..., help="Item ID."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Print a work item's frontmatter and body."""
    tasks_dir = get_tasks_dir()
    path = find_item_by_id(tasks_dir, item_id)
    if path is None:
        typer.echo(f"Error: item not found: {item_id}", err=True)
        raise typer.Exit(1)
    body_file = path / special_file_name(item_id) if path.is_dir() else path
    if not body_file.exists():
        typer.echo(f"Error: body file missing: {body_file}", err=True)
        raise typer.Exit(1)
    data, body = read_file(body_file)

    if as_json:
        payload = {
            "frontmatter": _to_plain(data),
            "body": body,
            "path": str(path),
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    typer.echo(body_file.read_text(encoding="utf-8"))
