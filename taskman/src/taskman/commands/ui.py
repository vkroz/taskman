"""Local task creation and live visibility into the shared work-item tree."""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import typer

from taskman import lifecycle
from taskman.model.collection import collection, query
from taskman.storage.store import Item, Store, StorageError, open_store, key_order


# ---- data preparation ----

VIEWS = ("attention", "active", "ready", "blocked", "drafts", "backlog", "done")
# Sort names the UI offers, and the row field each one orders by.
SORTS = {"manual": "priority", "key": "id", "title": "title", "state": "state", "type": "type",
         "assignee": "assignee", "created": "created_at", "updated": "updated_at"}
_KEY_PATH = re.compile(r"^/[A-Z][A-Z0-9]*-\d+$")


def _derive(items: dict[str, Item]) -> dict[str, dict[str, Any]]:
    """Signals for each item: state, blockers, leaf progress, verification, attention and last update."""
    children: dict[str, list[str]] = {}
    for item in items.values():
        if item.parent_id in items:
            children.setdefault(item.parent_id, []).append(item.id)

    def leaves(item_id: str) -> list[Item]:
        found = []
        for child in children.get(item_id, []):
            found.extend(leaves(child) if child in children else [items[child]])
        return found

    derived = {}
    for item in items.values():
        entries = item.metadata.get("journal") or []
        if item.status == "done":
            state = "done"
        elif item.draft:
            state = "draft"
        elif lifecycle.is_ready(item, items):
            state = item.status if item.status == "active" else "ready"
        else:
            state = "blocked"
        checks = [e for e in entries if e.get("kind") == "verification"]
        verification = None
        if checks:
            stale = checks[-1].get("revision") != item.metadata.get("revision", 0)
            verification = "stale" if stale else checks[-1].get("result")
        attention = None
        if state != "done":
            if verification == "fail":
                attention = "verification_failed"
            elif verification == "blocked":
                attention = "verification_blocked"
            elif entries and entries[-1].get("kind") == "handoff":
                attention = "handoff"
            elif verification == "pass" and all(g["ok"] for g in lifecycle.close_gates(item, items)):
                attention = "ready_to_close"
        progress = None
        if item.type in ("epic", "feat"):
            found = leaves(item.id)
            progress = {"done": sum(leaf.status == "done" for leaf in found), "total": len(found)}
        changed_at = str(item.metadata.get("changed_at") or "")
        derived[item.id] = {
            "state": state,
            "blockers": [] if state == "done" else lifecycle.blockers(item, items),
            "progress": progress,
            "verification": verification,
            "attention": attention,
            "updated_at": max([changed_at, *(str(e.get("at") or "") for e in entries)]),
        }
    return derived


def _in_view(view: str, item: Item, signals: dict[str, Any]) -> bool:
    if view == "attention":
        return signals["attention"] is not None
    if view == "drafts":
        return signals["state"] == "draft"
    if view in ("ready", "blocked"):
        return signals["state"] == view
    return item.status == view  # active, backlog, done


def _snapshot(store: Store) -> tuple[dict[str, Item], list[dict], dict[str, dict[str, Any]]]:
    """Items, ordered collection rows with derived state and updated time, and derived signals."""
    items = {item.id: item for item in store.items()}
    derived = _derive(items)
    rows = collection(store)
    for row in rows:
        # Sorting and filtering read derived values from the row metadata.
        signals = derived[row["id"]]
        row["metadata"] = {**row["metadata"], "state": signals["state"], "updated_at": signals["updated_at"]}
    return items, rows, derived


def _slim(row: dict, signals: dict[str, Any]) -> dict[str, Any]:
    """The row fields the workspace shows: no body, journal or internal fields."""
    meta = row["metadata"]
    return {
        "id": row["id"], "project": meta.get("project"), "parent_id": row["parent_id"],
        "order": row.get("order", 0), "depth": row.get("depth", 0), "context": row.get("context", False),
        "type": meta.get("type"), "title": meta.get("title"), "status": meta.get("status"),
        "draft": bool(meta.get("draft")), "assignee": meta.get("assignee"), "labels": meta.get("labels") or [],
        "depends_on": meta.get("depends_on") or [], "created_at": meta.get("created_at"),
        "changed_at": meta.get("changed_at"), **signals,
    }


def _row(items: dict[str, Item], rows: list[dict], derived: dict[str, dict[str, Any]], item_id: str) -> dict[str, Any]:
    """One item's workspace row, outside any view."""
    row = next(row for row in rows if row["id"] == item_id)
    depth = len(lifecycle.ancestors(items, items[item_id]))
    return _slim({**row, "depth": depth}, derived[item_id])


def workspace(store: Store, data: dict) -> dict[str, Any]:
    """The ``/api/query`` result: projects, view counts, facets and the rows of one view."""
    project, view = data.get("project") or "", data.get("view") or ""
    keyword, epic = data.get("keyword", ""), data.get("epic", "")
    sort, descending = data.get("sort") or "manual", bool(data.get("descending", False))
    if not isinstance(project, str) or not isinstance(view, str):
        raise ValueError("Project and view must be text")
    if view and view not in VIEWS:
        raise ValueError(f"Unknown view: {view}")
    if sort not in SORTS:
        raise ValueError(f"Unknown sort: {sort}")
    items, rows, derived = _snapshot(store)
    scoped = [item for item in items.values() if not project or item.metadata.get("project") == project]
    counts = {name: sum(_in_view(name, item, derived[item.id]) for item in scoped) for name in VIEWS}
    in_view = [item for item in scoped if not view or _in_view(view, item, derived[item.id])]
    facets: dict[str, dict[str, int]] = {"assignee": {}, "labels": {}, "type": {}, "state": {}}
    for item in in_view:
        values = {"assignee": [item.metadata.get("assignee")] if item.metadata.get("assignee") else [],
                  "labels": item.metadata.get("labels") or [], "type": [item.type],
                  "state": [derived[item.id]["state"]]}
        for facet, names in values.items():
            for name in names:
                facets[facet][name] = facets[facet].get(name, 0) + 1
    members = {item.id for item in in_view}
    matched = query(rows, data.get("filters") or [], keyword, epic, SORTS[sort], descending,
                    where=lambda row: row["id"] in members)
    projects = store.projects()
    # Group rows by project in project order; the stable sort keeps each project's tree order.
    position = {p["key"]: index for index, p in enumerate(projects)}
    matched.sort(key=lambda row: position.get(row["metadata"].get("project"), len(position)))
    open_count: dict[str, int] = {}
    for item in items.values():
        if item.status != "done":
            key = item.metadata.get("project")
            open_count[key] = open_count.get(key, 0) + 1
    result = {
        "actor": store.actor,
        "projects": [{**p, "open": open_count.get(p["key"], 0)} for p in projects],
        "counts": counts,
        "facets": {facet: dict(sorted(values.items())) for facet, values in facets.items()},
        "rows": [_slim(row, derived[row["id"]]) for row in matched],
    }
    return _tagged(result, data.get("etag"))


def _tagged(result: dict[str, Any], etag: Any) -> dict[str, Any]:
    """The result with its ``etag``, or only ``unchanged`` when the client already has it."""
    current = hashlib.sha1(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
    if etag == current:
        return {"etag": current, "unchanged": True}
    return {"etag": current, **result}


def show(store: Store, item_id: str, etag: str | None = None) -> dict[str, Any] | None:
    """The ``/api/show`` result, or ``None`` when the item does not exist."""
    items, rows, derived = _snapshot(store)
    item = items.get(item_id)
    if item is None:
        return None
    order = {row["id"]: row.get("order", 0) for row in rows}

    def brief(other_id: str) -> dict[str, Any]:
        other = items.get(other_id)
        return {"id": other_id, "title": other.title if other else "",
                "state": derived[other_id]["state"] if other else None}

    ancestors = list(reversed(lifecycle.ancestors(items, item)))
    children = sorted((other for other in items.values() if other.parent_id == item_id),
                      key=lambda other: (order[other.id], key_order(other.id)))
    blocker = lifecycle.discard_blocker(item, items)
    return _tagged({
        "frontmatter": item.metadata, "body": item.body, "path": item.location,
        "activity": store.activity(item_id),
        "row": _row(items, rows, derived, item_id),
        "ancestors": [{"id": a.id, "type": a.type, "title": a.title} for a in ancestors],
        "children": [{"id": c.id, "type": c.type, "title": c.title, "state": derived[c.id]["state"],
                      "assignee": c.metadata.get("assignee")} for c in children],
        "depends_on": [brief(dep) for dep in item.depends_on],
        "dependents": [brief(other.id) for other in sorted(items.values(), key=lambda o: key_order(o.id))
                       if item_id in other.depends_on],
        "close_gates": None if item.status == "done" else lifecycle.close_gates(item, items),
        "can_discard": blocker is None,
        "discard_blocked_reason": blocker,
    }, etag)


def edit_conflict(store: Store, data: dict) -> dict[str, Any] | None:
    """The 409 body when the stored title or body differs from the base the editor opened."""
    item = store.get(str(data.get("id", "")))
    if item is None:
        return None
    base_title, base_body = data.get("base_title"), data.get("base_body")
    if (base_title is None or base_title == item.title) and (base_body is None or base_body == item.body):
        return None
    changes = [a for a in store.activity(item.id) if a["field"] in ("title", "description")]
    actor = changes[-1]["author"] if changes else item.metadata.get("created_by")
    changed_at = changes[-1]["at"] if changes else item.metadata.get("created_at")
    return {"error": f"{item.id} was changed by {actor} after you opened it",
            "current": {"title": item.title, "body": item.body, "changed_at": changed_at, "actor": actor}}


def render_page() -> str:
    return Path(__file__).with_name("workspace.html").read_text(encoding="utf-8")


# ---- server ----


def _pid_file() -> Path:
    return Path(tempfile.gettempdir()) / "taskman_ui.pid"


def _unchanged_ok(change) -> dict:
    """Run a change; a request for the current state is not an error."""
    try:
        change()
    except lifecycle.NoChange as exc:
        return {'ok': True, 'message': exc.message}
    return {'ok': True}


def _make_handler(store: Store):

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # Silence default access log; uncomment for debugging.
            return

        def _local_request(self):
            authority = self.headers.get('Host', '')
            return authority in {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}

        def _json(self, status, data):
            body = json.dumps(data, default=str, separators=(',', ':')).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self._local_request() or self.headers.get('X-Taskman') != '1':
                self._json(403, {'error': 'Only local Taskman UI requests are accepted.'})
                return
            if self.path not in ('/api/items', '/api/query', '/api/edit', '/api/discard', '/api/reorder',
                                 '/api/comment', '/api/assign', '/api/label', '/api/fields'):
                self._json(404, {'error': 'Unknown endpoint'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 131072:
                    raise ValueError('Invalid request size')
                if self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Expected JSON')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Expected an object')
                if self.path != '/api/items':
                    from taskman.model.ordering import reorder_item
                    status = 200
                    item_id = str(data.get('id', ''))
                    with store.lock():
                        if self.path == '/api/query':
                            result = workspace(store, data)
                        elif self.path == '/api/edit':
                            conflict = edit_conflict(store, data)
                            if conflict:
                                status, result = 409, conflict
                            else:
                                lifecycle.edit(store, item_id, data.get('title'), data.get('body'), data.get('reason'))
                                result = {'ok': True}
                        elif self.path == '/api/comment':
                            kind, result_value = data.get('kind'), data.get('result') or None
                            author = store.actor
                            lifecycle.check_entry(kind, author, result_value)
                            text = data.get('text')
                            if not isinstance(text, str) or not text.strip():
                                raise ValueError('Enter the comment text')
                            lifecycle.record(store, item_id, kind, author, text, result_value)
                            result = {'ok': True}
                        elif self.path == '/api/assign':
                            assignee = data.get('assignee') or None
                            if assignee is not None and not isinstance(assignee, str):
                                raise ValueError('Enter an assignee name')
                            result = _unchanged_ok(lambda: lifecycle.assign(store, item_id, assignee))
                        elif self.path == '/api/label':
                            add, remove = data.get('add') or [], data.get('remove') or []
                            if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in (add, remove)):
                                raise ValueError('Labels must be lists of text')
                            result = _unchanged_ok(lambda: lifecycle.label(store, item_id, add, remove))
                        elif self.path == '/api/fields':
                            # One transaction: an error in either field rolls back both.
                            assignee, labels = data.get('assignee') or None, data.get('labels')
                            if assignee is not None and not isinstance(assignee, str):
                                raise ValueError('Enter an assignee name')
                            if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
                                raise ValueError('Labels must be a list of text')
                            current = store.get(item_id)
                            if current is None:
                                raise ValueError(f'item not found: {item_id}')
                            removed = [name for name in current.metadata.get('labels') or [] if name not in labels]
                            _unchanged_ok(lambda: lifecycle.label(store, item_id, labels, removed))
                            _unchanged_ok(lambda: lifecycle.assign(store, item_id, assignee))
                            result = {'ok': True}
                        elif self.path == '/api/discard':
                            lifecycle.discard(store, item_id)
                            result = {'ok': True}
                        else:
                            position = data.get('position')
                            if not isinstance(data.get('id'), str):
                                raise ValueError('Select a valid task ID')
                            result = {'order': reorder_item(store, item_id, position)}
                        if status == 200 and self.path in ('/api/edit', '/api/comment', '/api/assign',
                                                           '/api/label', '/api/fields'):
                            result['row'] = _row(*_snapshot(store), item_id)
                    self._json(status, result)
                    return
                title, kind = data.get('title', ''), data.get('type', 'task')
                body, parent = data.get('body', ''), data.get('parent') or None
                if not isinstance(title, str) or not title.strip() or len(title) > 200:
                    raise ValueError('Enter a title of 1–200 characters')
                if kind not in ('task', 'feat', 'epic') or not isinstance(body, str):
                    raise ValueError('Invalid task type or description')
                project = data.get('project') or None
                if parent is not None and not isinstance(parent, str):
                    raise ValueError('Invalid parent ID')
                if project is not None and not isinstance(project, str):
                    raise ValueError('Invalid project key')
                with store.lock():
                    item = lifecycle.create(store, title.strip(), kind, parent, body or None,
                                            project=project.strip() if project else None)
                    row = _row(*_snapshot(store), item.id)
                self._json(201, {'id': item.id, 'path': item.location, 'row': row})
            except (ValueError, StorageError, OSError, TypeError, KeyError) as exc:
                self._json(400, {'error': str(exc)})

        def do_GET(self) -> None:  # noqa: N802
            if not self._local_request():
                self._json(403, {'error': 'Only local Taskman UI requests are accepted.'})
                return
            try:
                # Readers share the write lock so a page never mixes two states.
                with store.lock():
                    self._get()
            except Exception as exc:  # noqa: BLE001 - the client always gets JSON
                self._json(500, {'error': str(exc)})

        def _get(self):
            url = urlsplit(self.path)
            path = url.path
            if path == "/" or _KEY_PATH.match(path):
                self._send_html(render_page())
            elif path.startswith("/api/show/"):
                etag = parse_qs(url.query).get("etag", [None])[0]
                self._send_show(path[len("/api/show/") :], etag)
            else:
                self._json(404, {'error': f'Not found: {path}'})

        def _send_html(self, html_str: str) -> None:
            body = html_str.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_show(self, item_id: str, etag: str | None) -> None:
            item = store.get(item_id)
            if item is None:
                self._json(404, {'error': f'item not found: {item_id}'})
                return
            if item.error:
                self._json(500, {'error': item.error})
                return
            self._json(200, show(store, item_id, etag))

    return _Handler


def _start_server(host: str, port: int, store: Store, open_browser: bool = True) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _make_handler(store))
    _pid_file().write_text(str(os.getpid()), encoding="utf-8")
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    return server


def _stop_server() -> bool:
    """Return True if a server was running and stopped, False otherwise."""
    pf = _pid_file()
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text())
        os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    pf.unlink(missing_ok=True)
    return True


def ui(
    port: int = typer.Option(8080, "--port", help="Port to bind on localhost."),
    stop: bool = typer.Option(False, "--stop", help="Stop a running UI server."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Serve without opening a browser."),
) -> None:
    """Create tasks and browse live agent progress in a local web UI."""
    if stop:
        if _stop_server():
            typer.echo("Stopped UI server.")
        else:
            typer.echo("No running UI server.")
        return

    typer.echo(f"Serving taskman UI at http://localhost:{port}/  (Ctrl+C to stop)")
    server = _start_server("127.0.0.1", port, open_store(), open_browser=not no_browser)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nShutting down.")
    finally:
        server.shutdown()
        server.server_close()
        _pid_file().unlink(missing_ok=True)
