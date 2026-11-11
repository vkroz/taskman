"""`auth` commands — log in to a Taskman server, log out, and show the login.

``login`` opens a browser by default. On a machine without a browser,
``login --with-token`` reads a personal access token from standard input.
The token goes to the CLI's login file; no command prints it.
"""
from __future__ import annotations

import html
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit

import typer

from taskman.login import challenge_for, page
from taskman.storage.credentials import credentials_path, forget_login, save_login, saved_token
from taskman.storage.remote import RemoteStore, check_server_url, server_url
from taskman.storage.store import StorageConfigError, StorageError

auth_app = typer.Typer(help="Log in to a Taskman server.", no_args_is_help=True)

LOGIN_WAIT_SECONDS = 300


def _fail(message: str, code: int = 1) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


def browser_login(url: str, label: str, open_browser: bool = True) -> str:
    """Sign in through the browser and return a new token for this machine."""
    wait = LOGIN_WAIT_SECONDS
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    answer: dict[str, str] = {}
    done = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            address = urlsplit(self.path)
            if address.path != "/callback" or done.is_set():
                self.send_error(404)
                return
            answer.update(parse_qsl(address.query))
            ok = answer.get("state") == state and bool(answer.get("code"))
            text = ("You are logged in to Taskman. You can close this tab." if ok
                    else "The login did not complete. Return to the terminal.")
            body = page("Taskman login", f"<h1>Taskman login</h1><p>{html.escape(text)}</p>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args) -> None:
            return

    listener = HTTPServer(("127.0.0.1", 0), Callback)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        start = f"{url}/auth/cli?" + urlencode({
            "port": listener.server_port, "state": state, "challenge": challenge_for(verifier), "label": label,
        })
        typer.echo(f"Open this address in a browser on this computer to log in:\n  {start}", err=True)
        if open_browser:
            webbrowser.open(start)
        if not done.wait(wait):
            _fail(f"No login within {wait:g} seconds. Run `taskman auth login` again.")
    finally:
        listener.shutdown()
        listener.server_close()
    if answer.get("state") != state or not answer.get("code"):
        _fail("The browser returned a login that this command did not start. Run `taskman auth login` again.")
    return RemoteStore(url, "").exchange(answer["code"], verifier)["token"]


@auth_app.command("login")
def login(
    server: str | None = typer.Option(None, "--server", help="Server address. Default: the server of the last login."),
    with_token: bool = typer.Option(False, "--with-token", help="Read a personal access token from standard input."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the login address; do not open a browser."),
    label: str | None = typer.Option(None, "--label", help="Name of this machine on the server. Default: host name."),
) -> None:
    """Log in to a Taskman server. Later commands use it."""
    try:
        url = check_server_url(server or server_url() or "")
    except StorageConfigError as exc:
        if not (server or server_url()):
            _fail("Name the server: taskman auth login --server https://taskman.example.com", 2)
        _fail(str(exc), 2)
    try:
        if with_token:
            token = sys.stdin.readline().strip()
            if not token:
                _fail("Paste the token on standard input.", 2)
        else:
            token = browser_login(url, (label or socket.gethostname())[:60], open_browser=not no_browser)
        person = RemoteStore(url, token).whoami()
    except StorageConfigError as exc:
        _fail(str(exc), 2)
    except StorageError as exc:
        _fail(str(exc))
    save_login(url, token)
    typer.echo(f"Logged in to {url} as {person}.")


@auth_app.command("logout")
def logout() -> None:
    """Revoke the saved token on the server and remove it from this machine."""
    url = server_url()
    token = saved_token(url) if url else ""
    if not token:
        typer.echo("Not logged in.")
        return
    try:
        RemoteStore(url, token).logout()
    except (StorageConfigError, StorageError) as exc:
        typer.echo(f"Warning: the server did not revoke the token: {exc}", err=True)
    forget_login(url)
    typer.echo(f"Logged out of {url}.")
    if os.environ.get("TASKMAN_TOKEN", "").strip():
        typer.echo("TASKMAN_TOKEN is still set in the environment.", err=True)


@auth_app.command("status")
def status() -> None:
    """Show the server and the person of the login. Never shows the token."""
    url = server_url()
    if not url:
        typer.echo("Not logged in. Taskman uses the database on this computer.")
        raise typer.Exit(1)
    source = "TASKMAN_TOKEN" if os.environ.get("TASKMAN_TOKEN", "").strip() else str(credentials_path())
    try:
        url = check_server_url(url)
        token = os.environ.get("TASKMAN_TOKEN", "").strip() or saved_token(url)
        if not token:
            raise StorageConfigError(f"Not logged in to {url}. Run: taskman auth login")
        person = RemoteStore(url, token).whoami()
    except (StorageConfigError, StorageError) as exc:
        _fail(str(exc))
    typer.echo(f"Logged in to {url} as {person}. Token from {source}.")
