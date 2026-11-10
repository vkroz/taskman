"""`migrate` command — read the legacy bash-era layout, write the new one."""
from __future__ import annotations

from pathlib import Path

import typer

from taskman.storage.file_migrate import _derive_slug_from_filename  # noqa: F401 — kept for existing callers
from taskman.storage.file_migrate import MigrationError, migrate_tree


def migrate(
    source: Path = typer.Argument(..., help="Legacy tasks/ directory to read."),
    dest: Path = typer.Argument(..., help="Empty directory to write the new tree to."),
) -> None:
    """Migrate a legacy taskman tree to the new uniform recursive layout."""
    dest = dest.resolve()
    try:
        count = migrate_tree(source.resolve(), dest)
    except MigrationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if count == 0:
        typer.echo("(no items to migrate)")
        return
    typer.echo(f"Migrated {count} item(s) to {dest}")
