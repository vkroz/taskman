"""`db upgrade` command — create or upgrade the SQL database schema."""
from __future__ import annotations

import typer

from taskman.storage.schema import upgrade as upgrade_schema
from taskman.storage.settings import storage_settings
from taskman.storage.sql import connect
from taskman.storage.store import StorageConfigError

db_app = typer.Typer(help="Manage the SQL database of the sqlite and postgres backends.", no_args_is_help=True)


@db_app.command("upgrade")
def upgrade() -> None:
    """Create the schema in a new database, or upgrade an older one. Safe to run again."""
    try:
        settings = storage_settings()
        if settings.backend == "file":
            raise StorageConfigError("The file backend has no database. Set TASKMAN_BACKEND to sqlite or postgres.")
        conn, dialect = connect(settings)
        try:
            applied = upgrade_schema(conn, dialect)
        finally:
            conn.close()
    except StorageConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None
    if applied:
        typer.echo(f"Applied schema versions: {', '.join(str(v) for v in applied)}.")
    else:
        typer.echo("The database schema is current.")
