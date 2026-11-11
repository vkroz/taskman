"""`serve` and `token` commands — run the Taskman server and manage its access tokens.

Both commands open the database of the storage settings directly. They run on
the machine of the server, not on the machines that call it.
"""
from __future__ import annotations

import json
import os
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import typer

from taskman.storage.settings import storage_settings
from taskman.storage.sql_store import SqlStore, open_sql_store
from taskman.storage.store import StorageConfigError, StorageError

token_app = typer.Typer(help="Manage the access tokens of a Taskman server.", no_args_is_help=True)


@contextmanager
def _database() -> Iterator[SqlStore]:
    """The server's database. A settings or storage error ends the command."""
    try:
        store = open_sql_store(storage_settings())
    except StorageConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None
    try:
        yield store
    except StorageError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    finally:
        store.close()


def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Address to listen on. Use 0.0.0.0 in a container."),
    port: int | None = typer.Option(None, "--port", help="Port to listen on. Default: the PORT variable, or 8080."),
) -> None:
    """Run the Taskman server that the CLI calls."""
    from taskman.server import make_server

    if port is None:
        try:
            port = int(os.environ.get("PORT", "") or 8080)
        except ValueError:
            typer.echo("Error: PORT must be a number.", err=True)
            raise typer.Exit(2) from None
    from taskman.login import Logins, login_settings

    try:
        settings = login_settings()
    except StorageConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None
    with _database() as store:
        server = make_server(host, port, store, logins=Logins(store, settings) if settings else None)
        if settings:
            typer.echo(f"Browser login with Google is on for {settings.public_url}.")
        else:
            typer.echo("Browser login is off: TASKMAN_GOOGLE_CLIENT_ID is not set. Tokens work.")
        # serve_forever must not be stopped from its own thread.
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
        typer.echo(f"Taskman server listens on http://{host}:{server.server_port}/  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            typer.echo("\nShutting down.")
        finally:
            server.server_close()


@token_app.command("create")
def token_create(
    person: str = typer.Option(..., "--person", help="Name of the person the token stands for."),
    label: str = typer.Option("", "--label", help="Where the token is used, for example a machine name."),
    days: int = typer.Option(90, "--days", help="Days until the token expires."),
) -> None:
    """Create a token for a person. The token is shown one time."""
    from taskman.storage.accounts import create_token

    with _database() as store:
        token_id, token = create_token(store, person, label, days)
    typer.echo(f"Token {token_id} for {person.strip()}, valid for {days} days. Store it now; it is not shown again.", err=True)
    typer.echo(token)


@token_app.command("list")
def token_list(
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List tokens. The secret of a token is never shown."""
    from taskman.storage.accounts import list_tokens

    with _database() as store:
        tokens = list_tokens(store)
    if as_json:
        typer.echo(json.dumps(tokens, indent=2))
        return
    for row in tokens:
        state = "revoked" if row["revoked_at"] else f"expires {row['expires_at'] or 'never'}"
        typer.echo(f"{row['id']}  {row['person']}  {row['label'] or '-'}  {state}  last used {row['last_used_at'] or 'never'}")


@token_app.command("revoke")
def token_revoke(
    token_id: int = typer.Argument(..., help="Token ID from `taskman token list`."),
) -> None:
    """Revoke a token. The next call with it fails."""
    from taskman.storage.accounts import revoke_token

    with _database() as store:
        revoked = revoke_token(store, token_id)
    if not revoked:
        typer.echo(f"Error: no active token has the ID {token_id}.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Revoked token {token_id}.")
