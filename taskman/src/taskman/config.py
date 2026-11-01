"""Where the CLI finds the server URL and access token.

Only the environment and the user file ``~/.config/taskman/config.json`` are
trusted. A repository ``.taskman/config.json`` is ignored so a checkout cannot
choose the host that receives the access token, or hide the user's token.
"""
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


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_settings() -> Settings:
    """Environment variables override the user config. Repo config is not read."""
    path = user_config_path()
    found = _read_json(path) if path.is_file() else {}
    url = os.environ.get("TASKMAN_URL") or str(found.get("url") or "http://127.0.0.1:8080")
    env_token = os.environ.get("TASKMAN_ACCESS_TOKEN")
    if env_token is None:
        token = str(found.get("token") or "")
    else:
        token = env_token
    return Settings(
        url=url.strip().rstrip("/"),
        token=token.strip(),
        config_path=path if path.is_file() else None,
    )


def save_server_url(url: str) -> Path:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_json(path) if path.is_file() else {}
    current["url"] = url.strip().rstrip("/")
    payload = (json.dumps(current, indent=2) + "\n").encode("utf-8")
    # Create with 0600 before writing so the token is not briefly world-readable.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    path.chmod(0o600)
    return path
