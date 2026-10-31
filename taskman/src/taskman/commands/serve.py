"""Network server for a container or shared host.

``taskman ui`` stays on localhost. This command is the production-style
process: gunicorn, bound to ``0.0.0.0`` and ``PORT``, with the task tree on
``TASKMAN_TASKS_DIR``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import typer

from taskman.access import configured_access_token
from taskman.commands.new import get_tasks_dir

# Tests replace this so a successful configuration does not exec the test runner.
_exec = os.execvp


class ServeConfigError(ValueError):
    """The process cannot be exposed with the current environment."""


@dataclass(frozen=True, slots=True)
class ServeConfig:
    token: str
    port: int
    workers: int
    tasks_dir: Path

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
    """Resolve bind address, worker count, token, and task directory."""
    token = configured_access_token()
    if token is None:
        raise ServeConfigError(
            "TASKMAN_ACCESS_TOKEN is required to serve Taskman. "
            "Set it to a long random value and do not commit it."
        )
    port = _positive_int("PORT", os.environ.get("PORT", ""), 8080, 65535)
    workers = _positive_int("TASKMAN_WEB_WORKERS", os.environ.get("TASKMAN_WEB_WORKERS", ""), 2, 32)
    return ServeConfig(token=token, port=port, workers=workers, tasks_dir=get_tasks_dir())


def gunicorn_argv(config: ServeConfig) -> list[str]:
    """Command the container runs. The bind address is always all interfaces."""
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
        "taskman.wsgi:application",
    ]


def serve() -> None:
    """Serve the workspace on 0.0.0.0 for Docker or a shared machine.

    Requires TASKMAN_ACCESS_TOKEN. Listens on PORT (default 8080). Task files
    are read from TASKMAN_TASKS_DIR.
    """
    try:
        config = serve_config()
    except ServeConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    config.tasks_dir.mkdir(parents=True, exist_ok=True)
    argv = gunicorn_argv(config)
    typer.echo(f"Serving taskman on http://{config.bind}/  (tasks: {config.tasks_dir})")
    try:
        _exec(argv[0], argv)
    except FileNotFoundError:
        typer.echo(
            "gunicorn is not installed. Install the serve extra: pip install 'taskman[serve]'.",
            err=True,
        )
        raise typer.Exit(code=1) from None
