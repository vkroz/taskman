"""SQL backend: the work-item model of ``docs/data-model.md``.

Lifecycle rules stay in ``taskman.lifecycle``. This store translates the
item data that lifecycle writes into rows: changed fields become column
updates and activity entries, new journal entries become comments, and
``depends_on`` becomes dependency links.

One connection serves the store. ``lock()`` runs a lifecycle operation in
one transaction that holds the database write lock; a write outside
``lock()`` runs in a transaction of its own.
"""
from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from taskman.model.yaml_io import emit_frontmatter
from taskman.storage.schema import CURRENT_VERSION
from taskman.storage.settings import StorageSettings
from taskman.storage.sql import SQLITE, Dialect, connect, transaction
from taskman.storage.store import Item, StorageConfigError, StorageError

if TYPE_CHECKING:
    from taskman.integrity import Finding

_KEY = re.compile(r"^([A-Z][A-Z0-9]{1,9})-([1-9][0-9]*)$")
NO_CONSOLIDATION = "Consolidation is not available with a SQL backend (decision D8)."
# Item data that only dedicated operations or the database itself change.
_READ_ONLY = ("id", "project", "type", "status", "created_at", "created_by", "changed_at")
_ITEM_COLUMNS = (
    "i.id, i.project, i.number, i.type, i.title, i.description, i.status, i.draft, "
    "i.revision, i.assignee, i.created_at, i.created_by, i.changed_at, p.project, p.number"
)


def default_actor() -> str:
    """The git ``user.name``, the author of changes when no actor is given (decision M1)."""
    try:
        name = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or "unknown"


def open_sql_store(settings: StorageSettings, actor: str | None = None) -> SqlStore:
    conn, dialect = connect(settings, create=False)
    try:
        return SqlStore(conn, dialect, actor)
    except BaseException:
        conn.close()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(field: str, value: Any) -> str | None:
    """An activity value as text."""
    if value is None:
        return None
    if field == "draft":
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


class SqlStore:
    consolidation = False  # decision D8
    reorder_done = False  # decision M2

    def __init__(self, conn: Any, dialect: Dialect, actor: str | None = None) -> None:
        self.conn = conn
        self.dialect = dialect
        self.actor = actor or default_actor()
        self._mutex = threading.RLock()
        self._depth = 0
        with self._read():
            version = self._schema_version()
        if version < CURRENT_VERSION:
            raise StorageConfigError(
                f"The database schema is at version {version}; Taskman needs version {CURRENT_VERSION}. "
                "Run: taskman db upgrade"
            )
        if version > CURRENT_VERSION:
            raise StorageConfigError(
                f"The database schema is at version {version}; this Taskman knows version {CURRENT_VERSION}. "
                "Upgrade Taskman."
            )

    def close(self) -> None:
        self.conn.close()

    def _schema_version(self) -> int:
        """The applied schema version, or 0 without a schema. Reads only: no lock, no DDL."""
        if self.dialect is SQLITE:
            exists = self._q("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_versions'").fetchone()
        else:
            exists = self._q("SELECT to_regclass('schema_versions')").fetchone()[0]
        if not exists:
            return 0
        return self._q("SELECT MAX(version) FROM schema_versions").fetchone()[0] or 0

    # ---- transactions --------------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        """One transaction with the write lock; nested calls join it."""
        with self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            with transaction(self.conn, self.dialect):
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0

    @contextmanager
    def _read(self) -> Iterator[None]:
        """A consistent snapshot for a read outside a write transaction."""
        with self._mutex:
            if self._depth:
                yield
                return
            self.conn.execute("BEGIN" if self.dialect is SQLITE else "BEGIN ISOLATION LEVEL REPEATABLE READ")
            self._depth = 1
            try:
                yield
            finally:
                self._depth = 0
                self.conn.execute("COMMIT")

    def _q(self, statement: str, params: tuple = ()) -> Any:
        return self.conn.execute(self.dialect.sql(statement), params)

    # ---- reads ---------------------------------------------------------

    def items(self) -> list[Item]:
        with self._read():
            return self._load(None)

    def get(self, item_id: str) -> Item | None:
        with self._read():
            row_id = self._row_id(item_id)
            if row_id is None:
                return None
            return self._load(row_id)[0]

    def document(self, item_id: str) -> str:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return emit_frontmatter(item.metadata, item.body)

    def activity(self, item_id: str) -> list[dict[str, Any]]:
        """The item's field changes, oldest first."""
        with self._read():
            row_id = self._require(item_id)
            rows = self._q(
                "SELECT field, old_value, new_value, author, created_at FROM activity WHERE item_id = ? ORDER BY id",
                (row_id,),
            ).fetchall()
        return [{"field": f, "old": old, "new": new, "author": a, "at": at} for f, old, new, a, at in rows]

    def histories(self) -> list[dict]:
        return []

    def sibling_order(self) -> dict[str, list[str]]:
        with self._read():
            rows = self._q(
                "SELECT i.project, i.number, p.project, p.number FROM items i "
                "LEFT JOIN items p ON p.id = i.parent_id ORDER BY i.rank, i.project, i.number"
            ).fetchall()
        order: dict[str, list[str]] = {}
        for project, number, parent_project, parent_number in rows:
            parent = f"{parent_project}-{parent_number}" if parent_project else "root"
            order.setdefault(parent, []).append(f"{project}-{number}")
        return order

    def check_layout(self) -> list[Finding]:
        return []

    # ---- writes --------------------------------------------------------

    def create(
        self,
        *,
        item_type: str,
        title: str,
        body: str,
        parent_id: str | None,
        slug: str,
        status: str,
        draft: bool,
        project: str | None = None,
    ) -> Item:
        with self.lock():
            if parent_id is not None:
                parent_row = self._require(parent_id)
                project = self._q("SELECT project FROM items WHERE id = ?", (parent_row,)).fetchone()[0]
                rank_query = ("SELECT MAX(rank) FROM items WHERE parent_id = ?", (parent_row,))
            elif project is None:
                raise StorageError("a new top-level item needs a project key")
            else:
                parent_row = None
                rank_query = ("SELECT MAX(rank) FROM items WHERE parent_id IS NULL", ())
            now = _now()
            self._q(
                "INSERT INTO projects (key, name, created_at) VALUES (?, ?, ?) ON CONFLICT (key) DO NOTHING",
                (project, project, now),
            )
            number = self._q("SELECT next_number FROM projects WHERE key = ?", (project,)).fetchone()[0]
            self._q("UPDATE projects SET next_number = ? WHERE key = ?", (number + 1, project))
            rank = (self._q(*rank_query).fetchone()[0] or 0) + 1
            row_id = self._q(
                "INSERT INTO items (project, number, type, title, description, status, draft, parent_id, rank, "
                "created_at, created_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (project, number, item_type, title, body, status, int(draft), parent_row, rank, now, self.actor, now),
            ).fetchone()[0]
            self._activity(row_id, "created", None, title, now)
            return self._load(row_id)[0]

    def save(self, item_id: str, metadata: dict[str, Any], body: str) -> Item:
        with self.lock():
            row_id = self._require(item_id)
            current = self._load(row_id)[0]
            old = current.metadata
            unknown = sorted(set(metadata) - set(old))
            if unknown:
                raise StorageError(f"unknown item fields: {', '.join(unknown)}")
            for key in _READ_ONLY:
                if key in metadata and metadata[key] != old[key]:
                    raise StorageError(f"{key} cannot change through save")
            new = {**old, **metadata}
            now = _now()
            changed = False

            columns = {"title": new["title"], "description": body, "draft": bool(new["draft"]),
                       "assignee": new["assignee"]}
            previous = {"title": old["title"], "description": current.body,
                        "draft": old["draft"], "assignee": old["assignee"]}
            for field, value in columns.items():
                if value != previous[field]:
                    stored = int(value) if field == "draft" else value
                    self._q(f"UPDATE items SET {field} = ? WHERE id = ?", (stored, row_id))
                    self._activity(row_id, field, previous[field], value, now)
                    changed = True
            if int(new["revision"]) != old["revision"]:
                self._q("UPDATE items SET revision = ? WHERE id = ?", (int(new["revision"]), row_id))
                changed = True

            labels = sorted(set(new["labels"] or []))
            if labels != old["labels"]:
                self._q("DELETE FROM labels WHERE item_id = ?", (row_id,))
                for name in labels:
                    self._q("INSERT INTO labels (item_id, label) VALUES (?, ?)", (row_id, name))
                self._activity(row_id, "labels", old["labels"], labels, now)
                changed = True

            deps = sorted(set(new["depends_on"] or []), key=self._key_order)
            if deps != old["depends_on"]:
                targets = [self._require(dep) for dep in deps]
                self._q("DELETE FROM dependencies WHERE item_id = ?", (row_id,))
                for target in targets:
                    self._q("INSERT INTO dependencies (item_id, depends_on) VALUES (?, ?)", (row_id, target))
                self._activity(row_id, "dependencies", old["depends_on"], deps, now)
                changed = True

            journal = list(new["journal"] or [])
            stored_journal = old["journal"]
            if journal[: len(stored_journal)] != stored_journal:
                raise StorageError("comments cannot change or be removed")
            for entry in journal[len(stored_journal):]:
                self._q(
                    "INSERT INTO comments (item_id, kind, result, author, text, revision, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row_id, entry["kind"], entry.get("result"), entry["actor"], entry["text"],
                     int(entry["revision"]), entry.get("at") or now),
                )
                changed = True

            if changed:
                self._q("UPDATE items SET changed_at = ? WHERE id = ?", (now, row_id))
            return self._load(row_id)[0]

    def set_status(self, item_id: str, status: str) -> Item:
        return self._set_column(item_id, "status", status)

    def set_type(self, item_id: str, item_type: str) -> Item:
        return self._set_column(item_id, "type", item_type)

    def delete(self, item_id: str) -> None:
        """Delete the item with its comments, links, labels and activity."""
        with self.lock():
            self._q("DELETE FROM items WHERE id = ?", (self._require(item_id),))

    def save_order(self, parent_key: str, item_ids: list[str], moved_id: str) -> None:
        """Give only the moved item a rank between its new neighbours."""
        with self.lock():
            index = item_ids.index(moved_id)
            row_id = self._require(moved_id)
            # Decision M2: the change to done is an item's last change, rank included.
            if self._q("SELECT status FROM items WHERE id = ?", (row_id,)).fetchone()[0] == "done":
                raise StorageError("closed items are immutable; create a linked follow-up task")

            def rank(key: str) -> float:
                return self._q("SELECT rank FROM items WHERE id = ?", (self._require(key),)).fetchone()[0]

            before = rank(item_ids[index - 1]) if index > 0 else None
            after = rank(item_ids[index + 1]) if index + 1 < len(item_ids) else None
            if before is not None and after is not None:
                new_rank = (before + after) / 2
                if not before < new_rank < after:
                    raise StorageError("sibling ranks are too close to place this item between them")
            elif before is not None:
                new_rank = before + 1
            elif after is not None:
                new_rank = after - 1
            else:
                return
            old_rank = rank(moved_id)
            now = _now()
            self._q("UPDATE items SET rank = ?, changed_at = ? WHERE id = ?", (new_rank, now, row_id))
            self._activity(row_id, "rank", old_rank, new_rank, now)

    def history_link(self, item_id: str) -> str:
        raise StorageError(NO_CONSOLIDATION)

    def save_history(self, record: dict[str, Any]) -> dict[str, Any]:
        raise StorageError(NO_CONSOLIDATION)

    # ---- helpers -------------------------------------------------------

    def _row_id(self, key: str) -> int | None:
        match = _KEY.match(key) if isinstance(key, str) else None
        if match is None:
            return None
        row = self._q(
            "SELECT id FROM items WHERE project = ? AND number = ?", (match[1], int(match[2]))
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _key_order(key: str) -> tuple[str, int]:
        """The order in which the store returns keys: project, then number. Unknown keys sort first."""
        match = _KEY.match(key) if isinstance(key, str) else None
        return (match[1], int(match[2])) if match else ("", 0)

    def _require(self, key: str) -> int:
        row_id = self._row_id(key)
        if row_id is None:
            raise StorageError(f"item not found: {key}")
        return row_id

    def _set_column(self, item_id: str, field: str, value: str) -> Item:
        with self.lock():
            row_id = self._require(item_id)
            old = self._q(f"SELECT {field} FROM items WHERE id = ?", (row_id,)).fetchone()[0]
            if old != value:
                now = _now()
                self._q(f"UPDATE items SET {field} = ?, changed_at = ? WHERE id = ?", (value, now, row_id))
                self._activity(row_id, field, old, value, now)
            return self._load(row_id)[0]

    def _activity(self, row_id: int, field: str, old: Any, new: Any, now: str) -> None:
        self._q(
            "INSERT INTO activity (item_id, field, old_value, new_value, author, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (row_id, field, _text(field, old), _text(field, new), self.actor, now),
        )

    def _load(self, row_id: int | None) -> list[Item]:
        """Items with their links, labels and comments: one item, or all when ``row_id`` is None."""
        one = row_id is not None
        item_filter, child_filter = ("WHERE i.id = ?", "WHERE c.item_id = ?") if one else ("", "")
        params = (row_id,) if one else ()
        rows = self._q(
            f"SELECT {_ITEM_COLUMNS} FROM items i LEFT JOIN items p ON p.id = i.parent_id {item_filter} "
            "ORDER BY i.project, i.number",
            params,
        ).fetchall()
        deps: dict[int, list[str]] = {}
        for item, project, number in self._q(
            "SELECT c.item_id, t.project, t.number FROM dependencies c JOIN items t ON t.id = c.depends_on "
            f"{child_filter} ORDER BY t.project, t.number",
            params,
        ).fetchall():
            deps.setdefault(item, []).append(f"{project}-{number}")
        labels: dict[int, list[str]] = {}
        for item, name in self._q(f"SELECT c.item_id, c.label FROM labels c {child_filter}", params):
            labels.setdefault(item, []).append(name)
        # Sort here: the database collation (en_US on Postgres) orders "-" and "_" differently.
        for names in labels.values():
            names.sort()
        journal: dict[int, list[dict[str, Any]]] = {}
        for item, kind, result, author, text, revision, at in self._q(
            f"SELECT c.item_id, c.kind, c.result, c.author, c.text, c.revision, c.created_at FROM comments c "
            f"{child_filter} ORDER BY c.id",
            params,
        ):
            entry = {"kind": kind, "actor": author, "at": at, "text": text, "revision": revision}
            if result is not None:
                entry["result"] = result
            journal.setdefault(item, []).append(entry)
        return [self._item(row, deps, labels, journal) for row in rows]

    def _item(self, row: tuple, deps: dict, labels: dict, journal: dict) -> Item:
        (row_id, project, number, item_type, title, description, status, draft, revision, assignee,
         created_at, created_by, changed_at, parent_project, parent_number) = row
        key = f"{project}-{number}"
        metadata = {
            "id": key,
            "project": project,
            "type": item_type,
            "status": status,
            "title": title,
            "draft": bool(draft),
            "revision": revision,
            "depends_on": deps.get(row_id, []),
            "assignee": assignee,
            "labels": labels.get(row_id, []),
            "journal": journal.get(row_id, []),
            "created_at": created_at,
            "created_by": created_by,
            "changed_at": changed_at,
        }
        return Item(
            id=key,
            type=item_type,
            status=status,
            priority="00",
            slug=re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
            parent_id=f"{parent_project}-{parent_number}" if parent_project else None,
            metadata=metadata,
            body=description,
            location=f"{self.dialect.name}:{key}",
            modified=datetime.fromisoformat(changed_at).timestamp(),
        )
