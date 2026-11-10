"""taskman CLI entry point.

The CLI is wired to a typer app. Subcommands are registered by the
implementations under ``taskman.commands``. This module exposes ``main`` as
the console_scripts entry point declared in pyproject.toml.
"""
from __future__ import annotations

import typer

from taskman import __version__
from taskman.commands.close import close as _close
from taskman.commands.convert import convert as _convert
from taskman.commands.help_cmd import help_cmd as _help
from taskman.commands.list_cmd import list_items as _list
from taskman.commands.migrate import migrate as _migrate
from taskman.commands.move import move as _move
from taskman.commands.new import finalize as _finalize
from taskman.commands.new import new as _new
from taskman.commands.queries import dependents as _dependents
from taskman.commands.queries import ready as _ready
from taskman.commands.queries import waiting_on as _waiting_on
from taskman.commands.search import search as _search
from taskman.commands.show import show as _show
from taskman.commands.ui import ui as _ui
from taskman.commands.validate import validate as _validate
from taskman.commands.worktree_status import worktree_status as _worktree_status
from taskman.commands.context import context, update, record, depend, discard
from taskman.model.locking import mutating
from taskman.storage.settings import load_env_file
from taskman.storage.store import StorageConfigError, open_store
from taskman.commands.install import install
from taskman.commands.db import db_app
from taskman.commands.collection import browse, edit, consolidate, history, reorder

app = typer.Typer(
    name="taskman",
    help="Uniform recursive work-item management.",
    no_args_is_help=True,
)


# Commands that work without storage settings, so a wrong setting can be fixed.
_NO_STORAGE = {"help", "version", "install", "db"}


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
        open_store()
    except StorageConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None


# Register subcommands from the commands package.
app.command("new")(mutating(_new))
app.command("finalize")(mutating(_finalize))
app.command("list")(_list)
app.command("show")(_show)
app.command("dependents")(_dependents)
app.command("move")(mutating(_move))
app.command("close")(mutating(_close))
app.command("context")(context)
app.command("update")(mutating(update))
app.command("record")(mutating(record))
app.command("depend")(mutating(depend))
app.command("discard")(mutating(discard))
app.command("install")(install)
app.command("browse")(browse)
app.command("edit")(mutating(edit))
app.command("consolidate")(mutating(consolidate))
app.command("history")(history)
app.command("reorder")(mutating(reorder))
app.command("validate")(_validate)
app.command("ready")(_ready)
app.command("waiting-on")(_waiting_on)
app.command("search")(_search)
app.command("worktree-status")(_worktree_status)
app.command("ui")(_ui)
app.command("help")(_help)
# --- auxiliary or temporary commands ---
app.command("convert")(mutating(_convert))
app.command("migrate")(_migrate)
app.add_typer(db_app, name="db")

@app.command()
def version() -> None:
    """Print the taskman version and exit."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
