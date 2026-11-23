"""`locate` command: find the item of a key or a copied link, and check this checkout against it.

An agent runs it first when a person gives it a key or a link from Copy link.
It refuses a link to another server, and a checkout whose git remotes match no
repository of the item. Taskman compares the remotes in the same normal form
as `repo`, so the agent does not compare addresses itself.
"""
from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import urlsplit

import typer

from taskman import lifecycle
from taskman.storage.remote import server_url
from taskman.storage.store import open_store

_LINK = re.compile(r"^(?P<server>https?://[^/]+)/items/(?P<key>[^/?#]+)/?$")


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> str:
    """Scheme, host and port in lower case: the part of a server address that names the server.

    A default port is left out, so https://host:443 and https://host name the same server.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        _fail(f"the link has an invalid port: {url}")
    port_text = f":{port}" if port and port != _DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{(parts.hostname or '').lower()}{port_text}"


def _remotes() -> list[str]:
    """The fetch addresses of the git remotes of the current directory, in Taskman's normal form."""
    try:
        output = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    addresses = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == "(fetch)":
            try:
                addresses.append(lifecycle.repository_address(fields[1]))
            except lifecycle.LifecycleError:
                continue  # a local remote is not an address
    return sorted(set(addresses))


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def locate(target: str = typer.Argument(..., help="An item key, or a link from Copy link: SERVER/items/KEY.")) -> None:
    """Find the item of a key or a link and check that this directory is a checkout of its repositories.

    Prints JSON: the key, the server, the repositories of the item (own or inherited), the git remotes of this directory and the match. Fails when the link names another server, or when the remotes match no repository of the item. "repositories": [] means the item names none yet.
    """
    target = target.strip()
    link = _LINK.match(target)
    server = server_url()
    key = link["key"] if link else target
    if link:
        if not server:
            _fail(f"the link names the server {link['server']}, but this CLI uses a local database. "
                  f"Run `taskman auth login --server {link['server']}` or set TASKMAN_SERVER_URL.")
        if _origin(link["server"]) != _origin(server):
            _fail(f"the link names the server {link['server']}, but this CLI works with {server}. "
                  "Use the link of this server, or log in to the other one.")
    store = open_store()
    items = {item.id: item for item in store.items()}
    if key not in items:
        _fail(f"item not found: {key}")
    item = items[key]
    own = list(item.metadata.get("repositories") or [])
    inherited = lifecycle.inherited_repositories(item, items)
    repositories = own or (inherited["addresses"] if inherited else [])
    remotes = _remotes()
    match = next((address for address in remotes if address in repositories), None)
    result = {"key": key, "server": server or None, "repositories": repositories,
              "inherited_from": inherited["from"] if inherited and not own else None,
              "remotes": remotes, "match": match}
    if repositories and match is None:
        found = ", ".join(remotes) if remotes else "no git remote"
        _fail(f"this directory ({found}) is not a checkout of a repository of {key}: {', '.join(repositories)}. "
              "Start the session in a checkout of one of them.")
    typer.echo(json.dumps(result, indent=2))
