"""Local task creation and live visibility into the shared work-item tree."""
from __future__ import annotations

import html
import json
import os
import signal
import tempfile
import time
import webbrowser
from taskman import lifecycle
from taskman.commands.queries import _load_graph, is_ready
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import typer

from taskman.storage.store import Store, StorageError, open_store, key_order


# ---- data preparation ----


@dataclass(frozen=True, slots=True)
class _Item:
    item_id: str
    parent_id: str | None
    item_type: str
    status: str
    priority: str
    slug: str
    title: str
    path: str
    depends_on: tuple[str, ...]
    mtime: float
    draft: bool = False


@dataclass(slots=True)
class _Page:
    items: list[_Item] = field(default_factory=list)
    ready: list[_Item] = field(default_factory=list)
    blocked: list[_Item] = field(default_factory=list)
    recent: list[_Item] = field(default_factory=list)
    graph_mermaid: str = ""


def _load_items(store: Store) -> list[_Item]:
    return [
        _Item(
            item_id=item.id,
            parent_id=item.parent_id,
            item_type=item.type,
            status=item.status,
            priority=item.priority,
            slug=item.slug,
            title=item.title,
            path=item.location,
            depends_on=item.depends_on,
            mtime=item.modified,
            draft=item.draft,
        )
        for item in store.items()
        if not item.error
    ]


def collect_page(store: Store) -> _Page:
    items = _load_items(store)
    graph = _load_graph(store)
    page = _Page(items=sorted(items, key=lambda i: (i.priority, key_order(i.item_id))))

    # Ready / blocked: applies only to non-done items.
    for i in items:
        if i.status == "done" or i.draft:
            continue
        deps_done = i.item_id in graph and is_ready(graph[i.item_id], graph)
        if deps_done:
            page.ready.append(i)
        else:
            page.blocked.append(i)
    page.ready.sort(key=lambda i: (i.priority, key_order(i.item_id)))
    page.blocked.sort(key=lambda i: (i.priority, key_order(i.item_id)))

    # Recently done: status=done, mtime within last 7 days.
    cutoff = time.time() - 7 * 24 * 60 * 60
    page.recent = sorted(
        (i for i in items if i.status == "done" and i.mtime >= cutoff),
        key=lambda i: -i.mtime,
    )

    page.graph_mermaid = _build_mermaid(items)
    return page


def _build_mermaid(items: list[_Item]) -> str:
    """Build a Mermaid flowchart string from non-done items' depends_on edges."""
    open_items = [i for i in items if i.status != "done"]
    if not open_items:
        return "flowchart TD\n  empty[no open items]"
    lines = ["flowchart TD"]
    for i in open_items:
        label = f"{i.item_type}-{i.item_id}<br/>{html.escape(i.slug)}"
        lines.append(f'  {i.item_id}["{label}"]')
    for i in open_items:
        for d in i.depends_on:
            lines.append(f"  {d} --> {i.item_id}")
    return "\n".join(lines)


# ---- HTML rendering ----


_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 1rem;
       display: grid; grid-template-columns: minmax(0, 2fr) minmax(18rem, 1fr); gap: 1rem; }
header { grid-column: 1 / -1; padding-bottom: 0.5rem; border-bottom: 1px solid #ccc; }
input#search { width: 100%; padding: 0.4rem; font-size: 1rem; }
main { min-width: 0; overflow-y: auto; max-height: 90vh; }
aside { min-width: 0; overflow-wrap: anywhere; background: #f4f4f4; padding: 1rem; border-radius: 4px; overflow-y: auto;
        max-height: 90vh; font-family: monospace; white-space: pre-wrap; font-size: 0.9rem; }
section { margin-bottom: 1.5rem; }
section h2 { margin: 0.5em 0 0.3em 0; font-size: 1.1rem; color: #333; }
.item { display: grid; grid-template-columns: 3em 9ch 8em minmax(0, 1fr); gap: 0.5rem; padding: 0.2rem 0.4rem; cursor: pointer;
        border-radius: 3px; font-family: monospace; font-size: 0.9rem; }
.item:hover { background: #eef; }
.item .type { color: #888; min-width: 3em; }
.item .id { color: #666; }
.item .title { overflow-wrap: anywhere; }
.item .status { color: #c40; min-width: 5em; }
.empty { color: #888; font-style: italic; padding-left: 0.5rem; }
ul { list-style: none; padding-left: 1.3rem; }
form { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 1rem; }
label { display: flex; flex-direction: column; gap: .25rem; }
textarea { width: 24rem; max-width: 85vw; }
@media (max-width: 800px) { body { display: block; } .item { grid-template-columns: 3em 9ch minmax(0, 1fr); } .item .title { grid-column: 1 / -1; } }
pre.mermaid { background: white; padding: 0.5rem; border: 1px solid #eee; }
"""

_JS = """
let selected = null;
function filterItems() {
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.item').forEach(el => {
    el.style.display = el.dataset.search.includes(q) ? '' : 'none';
  });
}
async function preview(id) {
  selected = id;
  const r = await fetch(`/api/show/${id}`);
  if (r.status === 404) {
    selected = null;
    document.getElementById('preview').textContent = 'This draft was removed. Select another item.';
    return;
  }
  if (!r.ok) throw new Error('Could not load task.');
  const data = await r.json();
  const meta = data.frontmatter;
  const entries = (meta.journal || []).map(e =>
    `${e.kind} — ${e.actor}${e.result ? ' — ' + e.result : ''}\\n${e.text}`).join('\\n\\n');
  document.getElementById('preview').textContent =
    `${meta.title} (${meta.status}${meta.draft ? ', draft' : ''})\\n` +
    `Dependencies: ${(meta.depends_on || []).join(', ') || 'none'}\\n\\n` +
    data.body + (entries ? '\\n\\nActivity and verification\\n\\n' + entries : '');
}
async function refresh() {
  try {
    const r = await fetch('/api/page');
    if (!r.ok) throw new Error('Could not refresh tasks.');
    const data = await r.json();
    document.querySelector('main').innerHTML = data.sections;
    const select = document.getElementById('parent');
    const old = select.value;
    select.replaceChildren(new Option('No parent', ''));
    data.parents.forEach(p => select.add(new Option(p.title + ' (' + p.id + ')', p.id)));
    select.value = old;
    filterItems();
    if (selected) await preview(selected);
    document.getElementById('connection').textContent = 'Up to date';
    if (window.mermaid) {
      await mermaid.run({querySelector: '.mermaid'}).catch(() => {
        document.querySelector('#graph pre').textContent = 'Diagram unavailable. Dependencies are listed in each task.';
      });
    } else {
      document.querySelector('#graph pre').textContent = 'Diagram unavailable offline. Dependencies are listed in each task.';
    }
  } catch (e) {
    document.getElementById('connection').textContent = e.message;
  }
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('search').addEventListener('input', filterItems);
  document.querySelector('main').addEventListener('click', e => {
    const item = e.target.closest('.item');
    if (item) preview(item.dataset.id).catch(e => {
      document.getElementById('preview').textContent = e.message;
    });
  });
  document.getElementById('create').addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const fields = Object.fromEntries(new FormData(form));
      const r = await fetch('/api/items', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Taskman': '1'},
        body: JSON.stringify(fields)});
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Could not create task.');
      form.reset();
      document.getElementById('create-result').textContent = 'Created draft. Continue with your coding agent.';
      selected = data.id;
      await refresh();
    } catch (e) {
      document.getElementById('create-result').textContent = e.message;
    } finally { button.disabled = false; }
  });
  async function poll() {
    await refresh();
    setTimeout(poll, 3000);
  }
  poll();
});
"""


def _render_item(i: _Item) -> str:
    search_blob = html.escape(
        f"{i.item_id} {i.item_type} {i.status} {i.slug} {i.title}".lower()
    )
    return (
        f'<div class="item" data-id="{html.escape(i.item_id)}" data-search="{search_blob}">'
        f'<span class="type">{html.escape(i.item_type)}</span>'
        f'<span class="id">{html.escape(i.item_id)}</span>'
        f'<span class="status">{html.escape(i.status)}{" (draft)" if i.draft else ""}</span>'
        f'<span class="title">{html.escape(i.title)}</span>'
        f"</div>"
    )


def _render_section(title: str, items: list[_Item], section_id: str) -> str:
    if not items:
        body = '<div class="empty">(none)</div>'
    else:
        body = "\n".join(_render_item(i) for i in items)
    return f'<section id="{section_id}"><h2>{html.escape(title)}</h2>{body}</section>'


def _render_tree(items: list[_Item]) -> str:
    keys = {i.item_id for i in items}
    children: dict[str | None, list[_Item]] = {}
    for item in items:
        children.setdefault(item.parent_id if item.parent_id in keys else None, []).append(item)

    def branch(parent):
        return '<ul>' + ''.join('<li>' + _render_item(i) + branch(i.item_id) + '</li>'
                               for i in children.get(parent, [])) + '</ul>'
    return '<section id="tree"><h2>Nested tree</h2>' + branch(None) + '</section>'


def render_sections(page: _Page) -> str:
    return "\n".join([
        _render_tree(page.items),
        _render_section("Ready", page.ready, "ready"),
        _render_section("Blocked", page.blocked, "blocked"),
        _render_section("Recently done (7 days)", page.recent, "recent"),
        f'<section id="graph"><h2>Dependency graph</h2><pre class="mermaid">{html.escape(page.graph_mermaid)}</pre></section>',
    ])


def render_page(page: _Page) -> str:
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
            body = json.dumps(data, default=str).encode('utf-8')
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
                                 '/api/comment', '/api/assign', '/api/label'):
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
                    from taskman.model.collection import collection, query
                    from taskman.model.ordering import reorder_item
                    with store.lock():
                        if self.path == '/api/query':
                            rows = collection(store)
                            fields = sorted({k for r in rows for k in r['metadata']} | {'parent_id'})
                            result = {'rows': query(rows, data.get('filters', []), data.get('keyword', ''), data.get('epic', ''), data.get('sort', 'priority'), data.get('descending', False)), 'all': rows, 'fields': fields}
                        elif self.path == '/api/edit':
                            lifecycle.edit(store, str(data.get('id', '')), data.get('title'), data.get('body'), data.get('reason'))
                            result = {'ok': True}
                        elif self.path == '/api/comment':
                            kind, result_value = data.get('kind'), data.get('result') or None
                            author = store.actor
                            lifecycle.check_entry(kind, author, result_value)
                            text = data.get('text')
                            if not isinstance(text, str) or not text.strip():
                                raise ValueError('Enter the comment text')
                            lifecycle.record(store, str(data.get('id', '')), kind, author, text, result_value)
                            result = {'ok': True}
                        elif self.path == '/api/assign':
                            assignee = data.get('assignee') or None
                            if assignee is not None and not isinstance(assignee, str):
                                raise ValueError('Enter an assignee name')
                            result = _unchanged_ok(lambda: lifecycle.assign(store, str(data.get('id', '')), assignee))
                        elif self.path == '/api/label':
                            add, remove = data.get('add') or [], data.get('remove') or []
                            if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in (add, remove)):
                                raise ValueError('Labels must be lists of text')
                            result = _unchanged_ok(lambda: lifecycle.label(store, str(data.get('id', '')), add, remove))
                        elif self.path == '/api/discard':
                            lifecycle.discard(store, str(data.get('id', '')))
                            result = {'ok': True}
                        else:
                            item_id = data.get('id')
                            position = data.get('position')
                            if not isinstance(item_id, str):
                                raise ValueError('Select a valid task ID')
                            result = {'order': reorder_item(store, item_id, position)}
                    self._json(200, result)
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
                self._json(201, {'id': item.id, 'path': item.location})
            except (ValueError, StorageError, OSError, TypeError, KeyError) as exc:
                self._json(400, {'error': str(exc)})

        def do_GET(self) -> None:  # noqa: N802
            if not self._local_request():
                self.send_error(403)
                return
            # Readers share the write lock so a page never mixes two states.
            with store.lock():
                self._get()

        def _get(self):
            if self.path == "/":
                self._send_html(render_page(collect_page(store)))
            elif self.path == '/api/page':
                page = collect_page(store)
                self._json(200, {'sections': render_sections(page), 'parents': [
                    {'id': i.item_id, 'title': i.title} for i in page.items
                    if i.item_type != 'task' and i.status != 'done']})
            elif self.path.startswith("/api/show/"):
                item_id = self.path[len("/api/show/") :]
                self._send_show(item_id)
            else:
                self.send_error(404)

        def _send_html(self, html_str: str) -> None:
            body = html_str.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_show(self, item_id: str) -> None:
            item = store.get(item_id)
            if item is None:
                self.send_error(404, f"item not found: {item_id}")
                return
            if item.error:
                self.send_error(500, item.error)
                return
            data = {"frontmatter": item.metadata, "body": item.body, "path": item.location,
                    "activity": store.activity(item_id)}
            payload = json.dumps(data, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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
