"""Network server. gunicorn binds 0.0.0.0 and PORT after Postgres is ready.

``taskman migrate`` applies the same schema and exits, so a deploy can migrate
before it starts the new revision.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import typer

from taskman.access import configured_access_token
from taskman.db import database_url, migrate, wait_for_database

_exec = os.execvp


class ServeConfigError(ValueError):
    """The process cannot be exposed with the current environment."""


@dataclass(frozen=True, slots=True)
class ServeConfig:
    port: int
    workers: int

    @property
    def bind(self) -> str:
        return f"0.0.0.0:{self.port}"


def _positive_int(name: str, raw: str, default: int, upper: int) -> int:
    text = raw.strip() if raw else ""
    if not text:
        return default
    if not text.isdigit():
        raise ServeConfigError(f"{name} must be an integer.")
    value = int(text)
    if value < 1 or value > upper:
        raise ServeConfigError(f"{name} must be between 1 and {upper}.")
    return value


def serve_config() -> ServeConfig:
    if configured_access_token() is None:
        raise ServeConfigError(
            "TASKMAN_ACCESS_TOKEN is required to serve Taskman. "
            "Set it to a long random value and do not commit it."
        )
    try:
        database_url()
    except RuntimeError as exc:
        raise ServeConfigError(str(exc)) from exc
    port = _positive_int("PORT", os.environ.get("PORT", ""), 8080, 65535)
    workers = _positive_int("TASKMAN_WEB_WORKERS", os.environ.get("TASKMAN_WEB_WORKERS", ""), 2, 32)
    return ServeConfig(port=port, workers=workers)


def gunicorn_argv(config: ServeConfig) -> list[str]:
    return [
        "gunicorn",
        "--bind",
        config.bind,
        "--workers",
        str(config.workers),
        "--timeout",
        "60",
        "--access-logfile",
        "-",
        "--error-logfile",
        "-",
        "taskman.httpapp:application",
    ]


def migrate_command() -> None:
    """Apply the database schema and exit.

    Uses the same connection settings as ``taskman serve``. Safe to run more
    than once. Does not require TASKMAN_ACCESS_TOKEN and does not start a server.
    """
    try:
        url = database_url()
        wait_for_database(url)
        migrate(url)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Database schema is up to date.")


def serve() -> None:
    """Serve the API and flight-control page on 0.0.0.0.

    Requires TASKMAN_ACCESS_TOKEN and either TASKMAN_DATABASE_URL or the
    TASKMAN_DB_SOCKET, TASKMAN_DB_NAME, TASKMAN_DB_USER, and TASKMAN_DB_PASSWORD
    settings. Waits for Postgres, creates tables, then execs gunicorn on PORT
    (default 8080).
    """
    try:
        config = serve_config()
        url = database_url()
        wait_for_database(url)
        migrate(url)
    except (ServeConfigError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    argv = gunicorn_argv(config)
    typer.echo(f"Serving taskman on http://{config.bind}/")
    try:
        _exec(argv[0], argv)
    except FileNotFoundError:
        typer.echo(
            "gunicorn is not installed. Install the serve extra: pip install 'taskman[serve]'.",
            err=True,
        )
        raise typer.Exit(code=1) from None
