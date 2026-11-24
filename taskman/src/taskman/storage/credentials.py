"""The login file of the CLI: the server it uses and a token for each server.

The file is ``~/.config/taskman/credentials.json`` (``$XDG_CONFIG_HOME`` when
set). Only the user can read it: mode 0600 in a directory with mode 0700.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from taskman.storage.store import StorageConfigError


def credentials_path(environ: Mapping[str, str] = os.environ) -> Path:
    base = environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".config") / "taskman" / "credentials.json"


def load_credentials(environ: Mapping[str, str] = os.environ) -> dict[str, Any]:
    path = credentials_path(environ)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise StorageConfigError(f"Taskman cannot read {path}. Delete it and run: taskman auth login") from None
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any], environ: Mapping[str, str]) -> None:
    path = credentials_path(environ)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temporary, path)


def save_login(url: str, token: str, environ: Mapping[str, str] = os.environ) -> None:
    """Make ``url`` the server of the CLI and keep its token."""
    data = load_credentials(environ)
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    _write({"server": url, "tokens": {**tokens, url: token}}, environ)


def forget_login(url: str, environ: Mapping[str, str] = os.environ) -> bool:
    """Remove the token of ``url``. Returns ``False`` when there was none."""
    data = load_credentials(environ)
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    if url not in tokens:
        return False
    del tokens[url]
    server = data.get("server")
    _write({"tokens": tokens} | ({"server": server} if server and server != url else {}), environ)
    return True


def saved_token(url: str, environ: Mapping[str, str] = os.environ) -> str:
    tokens = load_credentials(environ).get("tokens")
    return str(tokens.get(url, "")) if isinstance(tokens, dict) else ""
