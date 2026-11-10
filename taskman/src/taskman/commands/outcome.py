"""Report lifecycle and storage outcomes the way every command reports them."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import typer

from taskman.lifecycle import LifecycleError, NoChange
from taskman.storage.store import StorageError


@contextmanager
def reported() -> Iterator[None]:
    """Turn lifecycle outcomes into CLI output and exit codes.

    No change: message, exit 0. Usage error: typer usage message, exit 2.
    Other rule or storage error: ``Error: ...`` on stderr, exit 1.
    """
    try:
        yield
    except NoChange as exc:
        typer.echo(exc.message)
        raise typer.Exit(0) from None
    except LifecycleError as exc:
        if exc.usage:
            raise typer.BadParameter(exc.message) from None
        typer.echo(f"Error: {exc.message}", err=True)
        raise typer.Exit(1) from None
    except StorageError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
