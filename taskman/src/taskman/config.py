"""Where the CLI finds the server URL and access token."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    url: str
    token: str
    config_path: Path | None


def user_config_path() -> Path:
    return Path.home() / ".config" / "taskman" / "config.json"


def _config_candidates() -> list[Path]:
    paths: list[Path] = []
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        paths.append(directory / ".taskman" / "config.json")
        if (directory / ".git").exists():
            break
    paths.append(user_config_path())
    return paths


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_settings() -> Settings:
    """Environment variables win over a config file. The token is optional here.

    Callers that talk to the API reject an empty token. ``taskman config`` can
    still print the resolved URL when the token is missing.
    """
    found: dict = {}
    found_path: Path | None = None
    for path in _config_candidates():
        if path.is_file():
            found = _read_json(path)
            found_path = path
            break
    url = os.environ.get("TASKMAN_URL") or str(found.get("url") or "http://127.0.0.1:8080")
    env_token = os.environ.get("TASKMAN_ACCESS_TOKEN")
    if env_token is None:
        token = str(found.get("token") or "")
    else:
        token = env_token
    return Settings(url=url.strip().rstrip("/"), token=token.strip(), config_path=found_path)


def save_server_url(url: str) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_json(path) if path.is_file() else {}
    current["url"] = url.strip().rstrip("/")
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return path
