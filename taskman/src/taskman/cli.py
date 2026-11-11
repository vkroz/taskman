"""taskman CLI entry point.

The CLI is wired to a typer app. Subcommands are registered by the
implementations under ``taskman.commands``. This module exposes ``main`` as
the console_scripts entry point declared in pyproject.toml.
"""
from __future__ import annotations

from functools import wraps

import typer

from taskman import __version__
from taskman.commands.close import close as _close
from taskman.commands.convert import convert as _convert
from taskman.commands.help_cmd import help_cmd as _help
from taskman.commands.list_cmd import list_items as _list
from taskman.commands.move import move as _move
from taskman.commands.new import finalize as _finalize
from taskman.commands.new import new as _new
from taskman.commands.queries import dependents as _dependents
from taskman.commands.queries import ready as _ready
from taskman.commands.queries import waiting_on as _waiting_on
from taskman.commands.search import search as _search
from taskman.commands.serve import serve as _serve
from taskman.commands.serve import token_app
from taskman.commands.auth import auth_app
from taskman.commands.show import show as _show
from taskman.commands.ui import ui as _ui
from taskman.commands.validate import validate as _validate
from taskman.commands.context import activity, assign, comment, context, update, depend, discard, label, unassign
from taskman.model.locking import mutating
from taskman.storage.settings import load_env_file
from taskman.storage.remote import remote_settings, server_url
from taskman.storage.settings import storage_settings
from taskman.storage.store import StorageConfigError, begin_command
from taskman.commands.install import install
from taskman.commands.db import db_app
from taskman.commands.collection import browse, edit, reorder

app = typer.Typer(
    name="taskman",
    help="Uniform recursive work-item management.",
    no_args_is_help=True,
)


# Commands that work without storage settings, so a wrong setting can be fixed.
# `serve` and `token` open the server's database themselves and report their own errors.
_NO_STORAGE = {"help", "version", "install", "db", "serve", "token", "auth"}


@app.callback()
def _root(ctx: typer.Context) -> None:
    """Root callback. Forces typer multi-command mode so subcommands are
    dispatched, not collapsed into a single root command. Loads ``.env`` and
    rejects wrong storage settings before a command runs.
    """
    load_env_file()
    if ctx.invoked_subcommand in _NO_STORAGE:
        return
    try:
        if server_url():
            remote_settings()
        else:
            storage_settings()
    except StorageConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None
    # The command and `mutating` share one store, opened on first use.
    ctx.call_on_close(begin_command())


def _storage_errors(command):
    """Report a database that cannot be opened (missing, wrong schema) as a settings error: exit 2."""

    @wraps(command)
    def wrapped(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except StorageConfigError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(2) from None

    return wrapped


# Register subcommands from the commands package.
app.command("new")(_storage_errors(mutating(_new)))
app.command("finalize")(_storage_errors(mutating(_finalize)))
app.command("list")(_storage_errors(_list))
app.command("show")(_storage_errors(_show))
app.command("dependents")(_storage_errors(_dependents))
app.command("move")(_storage_errors(mutating(_move)))
app.command("close")(_storage_errors(mutating(_close)))
app.command("context")(_storage_errors(context))
app.command("update")(_storage_errors(mutating(update)))
app.command("comment")(_storage_errors(mutating(comment)))
app.command("record", help="Alias of `comment`.")(_storage_errors(mutating(comment)))
app.command("assign")(_storage_errors(mutating(assign)))
app.command("unassign")(_storage_errors(mutating(unassign)))
app.command("label")(_storage_errors(mutating(label)))
app.command("activity")(_storage_errors(activity))
app.command("depend")(_storage_errors(mutating(depend)))
app.command("discard")(_storage_errors(mutating(discard)))
app.command("install")(install)
app.command("browse")(_storage_errors(browse))
app.command("edit")(_storage_errors(mutating(edit)))
app.command("reorder")(_storage_errors(mutating(reorder)))
app.command("validate")(_storage_errors(_validate))
app.command("ready")(_storage_errors(_ready))
app.command("waiting-on")(_storage_errors(_waiting_on))
app.command("search")(_storage_errors(_search))
app.command("ui")(_storage_errors(_ui))
app.command("help")(_help)
# --- auxiliary or temporary commands ---
app.command("convert")(_storage_errors(mutating(_convert)))
app.add_typer(db_app, name="db")
app.command("serve")(_serve)
app.add_typer(token_app, name="token")
app.add_typer(auth_app, name="auth")

@app.command()
def version() -> None:
    """Print the taskman version and exit."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
