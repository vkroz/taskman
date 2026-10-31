"""Shared-secret access check for the networked Taskman server.

The localhost ``taskman ui`` command does not use this module. ``taskman serve``
and the WSGI app refuse to treat an empty value as a credential.
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
