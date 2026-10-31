"""Shared-secret access check for the Taskman server.

``taskman serve`` refuses to start when this value is missing. The WSGI app
rejects API calls with the same rule. ``GET /health`` does not use it.
"""
from __future__ import annotations

import hashlib
import hmac
import os

_CONTROL_CHARACTERS = "\r\n\x00"


def configured_access_token() -> str | None:
    """Return the configured token, or None when the server must fail closed.

    Surrounding whitespace is removed so a trailing newline in an env file
    does not become part of the secret. Control characters are rejected so
    the value cannot break HTTP headers if a caller echoes it.
    """
    token = os.environ.get("TASKMAN_ACCESS_TOKEN", "").strip()
    if not token or any(character in token for character in _CONTROL_CHARACTERS):
        return None
    return token


def secrets_equal(provided: str, expected: str) -> bool:
    """Compare two secrets without a short-circuit on the first difference."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )
