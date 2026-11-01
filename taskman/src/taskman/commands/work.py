"""CLI commands that talk to the Taskman server over HTTP."""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlencode

import typer

from taskman.client import ApiError, client_from_env
from taskman.config import resolve_settings, save_server_url
from taskman.workingcopy import write_working_copy


def _exit(exc: Exception) -> None:
    message = getattr(exc, "message", None) or str(exc)
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _guard(fn):
    try:
        return fn()
    except (ApiError, OSError) as exc:
        _exit(exc)


def _task_id(value: str) -> int:
    if not str(value).isdigit():
        raise ApiError(f"Task id must be an integer: {value}")
    return int(value)


def _body(path: str | None) -> str:
    if not path:
        return ""
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def default_agent() -> str:
    """Name recorded when the caller does not pass one. Never empty."""
    host = socket.gethostname().split(".")[0].strip()
    return host or "taskman"


def _agent(value: str) -> str:
    """Name stored on an event. Never empty.

    Order is the flag, then ``TASKMAN_AGENT``, then the short hostname.
    """
    chosen = (value or "").strip() or os.environ.get("TASKMAN_AGENT", "").strip()
    return chosen or default_agent()


def _session(value: str) -> str:
    import os

    return value or os.environ.get("TASKMAN_SESSION", "")


def _echo_task(task: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(task))
        return
    typer.echo(f"{task['id']}\t{task['type']}\t{task['status']}\t{task['title']}")


def new(
    title: str = typer.Option(..., "--title", help="Task title."),
    item_type: str = typer.Option("task", "--type", help="task, feat, or epic."),
    parent: str | None = typer.Option(None, "--parent", help="Parent id."),
    body_file: str | None = typer.Option(None, "--body-file", help="Body file, or - for stdin."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a task on the Taskman server."""

    def run():
        payload = {
            "title": title,
            "type": item_type,
            "body": _body(body_file),
            "parent_id": _task_id(parent) if parent else None,
        }
        task = client_from_env().request("POST", "/api/tasks", payload)
        _echo_task(task, as_json)

    _guard(run)


def list_items(
    status: str | None = typer.Option(None, "--status", help="backlog, active, or done."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List tasks stored on the server."""

    def run():
        path = "/api/tasks" if not status else "/api/tasks?" + urlencode({"status": status})
        payload = client_from_env().request("GET", path)
        if as_json:
            typer.echo(json.dumps(payload["tasks"]))
            return
        for task in payload["tasks"]:
            _echo_task(task, False)

    _guard(run)


def show(task_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show one task."""

    def run():
        task = client_from_env().request("GET", f"/api/tasks/{_task_id(task_id)}")
        if as_json:
            typer.echo(json.dumps(task))
            return
        typer.echo(f"{task['id']} {task['type']} {task['status']}")
        typer.echo(task["title"])
        if task["body"]:
            typer.echo("")
            typer.echo(task["body"])

    _guard(run)


def update(
    task_id: str,
    title: str | None = typer.Option(None, "--title"),
    body_file: str | None = typer.Option(None, "--body-file"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Change a task title or body on the server."""

    def run():
        payload = {}
        if title is not None:
            payload["title"] = title
        if body_file is not None:
            payload["body"] = _body(body_file)
        if not payload:
            raise ApiError("Pass --title or --body-file.")
        task = client_from_env().request("PATCH", f"/api/tasks/{_task_id(task_id)}", payload)
        _echo_task(task, as_json)

    _guard(run)


def move(task_id: str, status: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Set a task status to backlog, active, or done."""

    def run():
        task = client_from_env().request("PATCH", f"/api/tasks/{_task_id(task_id)}", {"status": status})
        _echo_task(task, as_json)

    _guard(run)


def depend(
    task_id: str,
    on: list[str] | None = typer.Option(None, "--on", help="Dependency id. Repeat to set several."),
    clear: bool = typer.Option(False, "--clear", help="Remove dependencies."),
) -> None:
    """Replace a task's dependency list."""

    def run():
        if clear and on:
            raise ApiError("Pass either --clear or --on, not both.")
        if not clear and not on:
            raise ApiError("Pass --on ID or --clear.")
        ids = [] if clear else [_task_id(item) for item in on or []]
        result = client_from_env().request(
            "PUT", f"/api/tasks/{_task_id(task_id)}/dependencies", {"depends_on": ids}
        )
        typer.echo(json.dumps(result))

    _guard(run)


def ready(as_json: bool = typer.Option(False, "--json")) -> None:
    """List tasks whose dependencies are done."""

    def run():
        payload = client_from_env().request("GET", "/api/ready")
        if as_json:
            typer.echo(json.dumps(payload["tasks"]))
            return
        for task in payload["tasks"]:
            _echo_task(task, False)

    _guard(run)


def context(task_id: str) -> None:
    """Print a task with its parent, children, dependencies, and events."""

    def run():
        payload = client_from_env().request("GET", f"/api/tasks/{_task_id(task_id)}/context")
        typer.echo(json.dumps(payload))

    _guard(run)


def pull(
    task_id: str,
    agent: str = typer.Option("", "--agent", help="Name recorded on the pull event."),
    session: str = typer.Option("", "--session"),
) -> None:
    """Copy a task into .taskman/tasks/<id>.md and remember it as the active task."""

    def run():
        client = client_from_env()
        payload = client.request(
            "POST",
            f"/api/tasks/{_task_id(task_id)}/pull",
            {"agent": _agent(agent), "session": _session(session)},
        )
        path = write_working_copy(Path.cwd(), payload, client.base_url)
        typer.echo(str(path))
        typer.echo(
            "Working copy is under .taskman/ at the git repo root. That directory is listed in .gitignore.",
            err=True,
        )

    _guard(run)


def record(
    task_id: str,
    message: str = typer.Option(..., "--message"),
    agent: str = typer.Option("", "--agent"),
    session: str = typer.Option("", "--session"),
) -> None:
    """Attach a note to a task."""

    def run():
        event = client_from_env().request(
            "POST",
            f"/api/tasks/{_task_id(task_id)}/events",
            {
                "kind": "note",
                "message": message,
                "agent": _agent(agent),
                "session": _session(session),
            },
        )
        typer.echo(json.dumps(event))

    _guard(run)


def complete(
    task_id: str,
    message: str = typer.Option("", "--message"),
    agent: str = typer.Option("", "--agent"),
    session: str = typer.Option("", "--session"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark a task done and record a completion event."""

    def run():
        result = client_from_env().request(
            "POST",
            f"/api/tasks/{_task_id(task_id)}/complete",
            {"message": message, "agent": _agent(agent), "session": _session(session)},
        )
        _echo_task(result["task"], as_json)

    _guard(run)


def close(
    task_id: str,
    message: str = typer.Option("", "--message"),
    agent: str = typer.Option("", "--agent"),
    session: str = typer.Option("", "--session"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Alias of complete."""
    complete(task_id, message=message, agent=agent, session=session, as_json=as_json)


def activity(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show the flight-control rows the dashboard displays."""

    def run():
        payload = client_from_env().request("GET", "/api/dashboard")
        if as_json:
            typer.echo(json.dumps(payload))
            return
        for row in payload["rows"]:
            typer.echo(
                f"{row['id']}\t{row['status']}\t{row['agent'] or '-'}\t{row['last_event'] or '-'}\t{row['title']}"
            )

    _guard(run)


def report_commit(
    task: str | None = typer.Option(None, "--task"),
    sha: str = typer.Option("", "--sha"),
    message: str = typer.Option("", "--message"),
    agent: str = typer.Option("", "--agent"),
    session: str = typer.Option("", "--session"),
) -> None:
    """Report a commit for a task."""
    _send_event("commit", task, agent, session, commit_sha=sha, message=message)


def report_pr(
    task: str | None = typer.Option(None, "--task"),
    url: str = typer.Option(..., "--url"),
    message: str = typer.Option("", "--message"),
    agent: str = typer.Option("", "--agent"),
    session: str = typer.Option("", "--session"),
) -> None:
    """Report a pull request URL for a task."""
    _send_event("pull_request", task, agent, session, pr_url=url, message=message or url)


def report_completion(
    task: str | None = typer.Option(None, "--task"),
    message: str = typer.Option("", "--message"),
    agent: str = typer.Option("", "--agent"),
    session: str = typer.Option("", "--session"),
) -> None:
    """Report completion. Prefer `taskman complete` so the status becomes done."""

    def run():
        task_id = _resolve_task(task)
        client_from_env().request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            {"message": message, "agent": _agent(agent), "session": _session(session)},
        )
        typer.echo(f"completed {task_id}")

    _guard(run)


def _resolve_task(task: str | None) -> int:
    from taskman.workingcopy import active_task_id

    if task:
        return _task_id(task)
    found = active_task_id(Path.cwd())
    if not found:
        raise ApiError("No task id. Pass --task or run taskman pull ID first.")
    return _task_id(found)


def _send_event(kind: str, task: str | None, agent: str, session: str, **fields: str) -> None:
    def run():
        task_id = _resolve_task(task)
        event = client_from_env().request(
            "POST",
            f"/api/tasks/{task_id}/events",
            {"kind": kind, "agent": _agent(agent), "session": _session(session), **fields},
        )
        typer.echo(json.dumps(event))

    _guard(run)


def config_cmd(server: str | None = typer.Option(None, "--server", help="Save the server URL for this user.")) -> None:
    """Show or save the CLI's server URL. The token is not printed."""
    if server:
        if not server.startswith(("http://", "https://")):
            typer.echo("Server URL must start with http:// or https://.", err=True)
            raise typer.Exit(2)
        path = save_server_url(server)
        typer.echo(f"Saved Taskman server URL to {path}")
        return
    settings = resolve_settings()
    typer.echo(f"url={settings.url}")
    typer.echo("token=" + ("set" if settings.token else "missing"))


def validate() -> None:
    """Check that the server is reachable and the token is accepted."""

    def run():
        client = client_from_env()
        health = client.health()
        if health.get("status") != "ok":
            raise ApiError("Taskman health check did not return ok.")
        client.request("GET", "/api/tasks")
        typer.echo("OK")

    _guard(run)


def ui() -> None:
    """Print the flight-control URL. The server is started with taskman serve or Compose."""
    typer.echo(resolve_settings().url + "/")
