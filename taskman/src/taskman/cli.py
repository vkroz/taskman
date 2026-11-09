"""taskman CLI entry point.

Commands talk to a Taskman server over HTTP. ``taskman serve`` is that server.
"""
from __future__ import annotations

import typer

from taskman import __version__
from taskman.commands.help_cmd import help_cmd as _help
from taskman.commands.hook_cmd import hook_app, hooks_app
from taskman.commands.install import install
from taskman.commands.serve import migrate_command as _migrate
from taskman.commands.serve import serve as _serve
from taskman.commands.work import (
    activity,
    close,
    complete,
    config_cmd,
    context,
    depend,
    list_items,
    move,
    new,
    pull,
    ready,
    record,
    report_commit,
    report_completion,
    report_pr,
    show,
    ui,
    update,
    validate,
)

app = typer.Typer(
    name="taskman",
    help="Taskman client and server. Tasks live in Postgres behind the server.",
    no_args_is_help=True,
)
report_app = typer.Typer(help="Report an agent event to the server.")


@app.callback()
def _root() -> None:
    """Root callback. Forces typer multi-command mode."""


app.command("new")(new)
app.command("list")(list_items)
app.command("show")(show)
app.command("update")(update)
app.command("move")(move)
app.command("depend")(depend)
app.command("ready")(ready)
app.command("context")(context)
app.command("pull")(pull)
app.command("record")(record)
app.command("complete")(complete)
app.command("close")(close)
app.command("activity")(activity)
app.command("validate")(validate)
app.command("ui")(ui)
app.command("config")(config_cmd)
app.command("install")(install)
app.command("migrate")(_migrate)
app.command("serve")(_serve)
app.command("help")(_help)
app.add_typer(report_app, name="report")
app.add_typer(hook_app, name="hook")
app.add_typer(hooks_app, name="hooks")
report_app.command("commit")(report_commit)
report_app.command("pr")(report_pr)
report_app.command("completion")(report_completion)


@app.command()
def version() -> None:
    """Print the taskman version and exit."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
