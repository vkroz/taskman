"""The --agent value: who authored the changes an event records.

Taskman does not control agents. ``--agent`` names the author of the changes
(a person, ``claude-code``, ``codex``, ...). It comes only from the command
line. There is no environment variable and no default.
"""
from __future__ import annotations

AGENT_REQUIRED = "--agent <author> is required: name who authored these changes"


class MissingAgent(ValueError):
    """The --agent value is missing or blank."""


def require_agent(value: str | None) -> str:
    """Return the trimmed --agent value, or raise :class:`MissingAgent`."""
    author = (value or "").strip()
    if not author:
        raise MissingAgent(AGENT_REQUIRED)
    return author
