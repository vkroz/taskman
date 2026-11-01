"""Agent name stored on events. The CLI and the hooks use this one resolver."""
from __future__ import annotations

import os
import socket


def default_agent() -> str:
    """Name used when nothing more specific is set. Never empty.

    Claude Code sets CLAUDECODE=1 in the commands it runs, so a CLI call from a
    Claude session records the same name as the Claude hook.
    """
    if os.environ.get("CLAUDECODE", "").strip() == "1":
        return "claude-code"
    host = socket.gethostname().split(".")[0].strip()
    return host or "taskman"


def resolve_agent(value: str | None = "", fallback: str = "") -> str:
    """Return a non-empty agent name. Every candidate is stripped.

    Order: ``value`` (the --agent flag), then TASKMAN_AGENT, then the caller's
    ``fallback`` (a hook's own name), then :func:`default_agent`.
    """
    for candidate in (value, os.environ.get("TASKMAN_AGENT"), fallback):
        chosen = (candidate or "").strip()
        if chosen:
            return chosen
    return default_agent()
