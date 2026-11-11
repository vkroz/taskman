"""Persons and their access tokens, in the server's SQL database.

A token is a secret string that stands for one person. The database keeps
only a SHA-256 fingerprint of it, so a copy of the database holds no usable
token.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from taskman.storage.sql_store import SqlStore
from taskman.storage.store import StorageError

TOKEN_PREFIX = "tmn_"
_TOUCH_AFTER = timedelta(minutes=1)


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _q(store: SqlStore, statement: str, params: tuple = ()) -> Any:
    return store.conn.execute(store.dialect.sql(statement), params)


def create_token(store: SqlStore, person: str, label: str = "", days: int | None = None) -> tuple[int, str]:
    """Create a token for a person, adding the person when new. Returns the token ID and the token.

    The token is returned here only; it cannot be read back later.
    """
    person = person.strip()
    if not 1 <= len(person) <= 60:
        raise StorageError("a person name has 1 to 60 characters")
    if days is not None and days < 1:
        raise StorageError("a token is valid for 1 day or more")
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=days)).isoformat() if days else None
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with store.lock():
        _q(store, "INSERT INTO persons (name, created_at) VALUES (?, ?) ON CONFLICT (name) DO NOTHING",
           (person, now.isoformat()))
        person_id = _q(store, "SELECT id FROM persons WHERE name = ?", (person,)).fetchone()[0]
        token_id = _q(
            store,
            "INSERT INTO tokens (person_id, token_hash, label, created_at, expires_at) VALUES (?, ?, ?, ?, ?) "
            "RETURNING id",
            (person_id, _fingerprint(token), label.strip(), now.isoformat(), expires),
        ).fetchone()[0]
    return token_id, token


def person_for_token(store: SqlStore, token: str) -> str | None:
    """The person a valid token stands for, or ``None`` for an unknown, expired or revoked token."""
    now = datetime.now(timezone.utc)
    with store.lock():
        row = _q(
            store,
            "SELECT t.id, p.name, t.expires_at, t.revoked_at, t.last_used_at FROM tokens t "
            "JOIN persons p ON p.id = t.person_id WHERE t.token_hash = ?",
            (_fingerprint(token),),
        ).fetchone()
        if row is None:
            return None
        token_id, person, expires_at, revoked_at, last_used_at = row
        if revoked_at or (expires_at and datetime.fromisoformat(expires_at) <= now):
            return None
        if not last_used_at or now - datetime.fromisoformat(last_used_at) >= _TOUCH_AFTER:
            _q(store, "UPDATE tokens SET last_used_at = ? WHERE id = ?", (now.isoformat(), token_id))
    return person


def list_tokens(store: SqlStore) -> list[dict[str, Any]]:
    """Every token without its secret, oldest first."""
    with store.lock():
        rows = _q(
            store,
            "SELECT t.id, p.name, t.label, t.created_at, t.expires_at, t.last_used_at, t.revoked_at "
            "FROM tokens t JOIN persons p ON p.id = t.person_id ORDER BY t.id",
        ).fetchall()
    fields = ("id", "person", "label", "created_at", "expires_at", "last_used_at", "revoked_at")
    return [dict(zip(fields, row)) for row in rows]


def revoke_own_token(store: SqlStore, token: str) -> None:
    """Revoke the token itself, for a logout."""
    with store.lock():
        _q(store, "UPDATE tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
           (datetime.now(timezone.utc).isoformat(), _fingerprint(token)))


def revoke_token(store: SqlStore, token_id: int) -> bool:
    """Revoke a token. Returns ``False`` when no active token has this ID."""
    with store.lock():
        row = _q(store, "SELECT revoked_at FROM tokens WHERE id = ?", (token_id,)).fetchone()
        if row is None or row[0]:
            return False
        _q(store, "UPDATE tokens SET revoked_at = ? WHERE id = ?",
           (datetime.now(timezone.utc).isoformat(), token_id))
    return True
