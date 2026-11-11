"""Browser smoke tests of the workspace page against a seeded ``taskman ui`` server.

Each test drives Chromium through Playwright. A session server serves read-only
checks; tests that change data get their own server on a copy of the seed. The
tests are marked ``ui`` and skip when Playwright or its browser is missing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from taskman.storage.schema import upgrade
from taskman.storage.settings import StorageSettings
from taskman.storage.sql import connect
from taskman.storage.sql_store import open_sql_store
from tests.ui_seed import seed

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.ui

HEADERS = {"Content-Type": "application/json", "X-Taskman": "1"}
DESKTOP = {"viewport": {"width": 1440, "height": 900}}
MOBILE = {"viewport": {"width": 390, "height": 844}, "has_touch": True}
KEY = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


# ---- server and browser fixtures ----


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """``taskman ui`` on a SQLite database file, in its own process."""

    def __init__(self, db: Path, home: Path) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("TASKMAN_")}
        # TMPDIR keeps the server's pid file away from a real `taskman ui`.
        env.update(HOME=str(home), TMPDIR=str(home), TASKMAN_BACKEND="sqlite", TASKMAN_DATABASE_URL=f"sqlite://{db}")
        command = [str(Path(sys.executable).with_name("taskman")), "ui", "--port", str(self.port), "--no-browser"]
        self.process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(self.base + "/", timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline or self.process.poll() is not None:
                    self.stop()
                    raise RuntimeError("taskman ui did not start")
                time.sleep(0.1)

    def api(self, path: str, data: dict | None = None):
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(self.base + path, data=body, headers=HEADERS)
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("seed") / "seed.db"
    settings = StorageSettings("sqlite", sqlite_path=path)
    conn, dialect = connect(settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    store = open_sql_store(settings, actor="Vlad")
    seed(store)
    store.close()
    return path


def _copy(seed_db: Path, folder: Path) -> Path:
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(seed_db) + suffix)
        if source.exists():
            shutil.copy(source, folder / ("work.db" + suffix))
    return folder / "work.db"


@pytest.fixture(scope="session")
def ro(seed_db, tmp_path_factory):
    """A server for checks that change nothing."""
    folder = tmp_path_factory.mktemp("ro")
    server = Server(_copy(seed_db, folder), folder)
    yield server
    server.stop()


@pytest.fixture
def rw(seed_db, tmp_path):
    """A server on a fresh copy of the seed, for checks that change data."""
    server = Server(_copy(seed_db, tmp_path), tmp_path)
    yield server
    server.stop()


@pytest.fixture(scope="session")
def chromium():
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright cannot start: {str(exc).splitlines()[0]}")
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        playwright.stop()
        pytest.skip(f"Chromium cannot launch: {str(exc).splitlines()[0]}")
    yield browser
    browser.close()
    playwright.stop()


def _page(chromium, options, scheme="light"):
    context = chromium.new_context(color_scheme=scheme, **options)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    return context, page


@pytest.fixture
def desktop(chromium):
    context, page = _page(chromium, DESKTOP)
    yield page
    errors = page.errors
    context.close()
    assert not errors


@pytest.fixture
def mobile(chromium):
    context, page = _page(chromium, MOBILE)
    yield page
    errors = page.errors
    context.close()
    assert not errors


# ---- helpers ----


def visit(page, server, path="/"):
    page.goto(server.base + path)
    page.wait_for_selector("#rows .row, #empty:not([hidden])")


def row_ids(page, context=True):
    selector = "#rows .row" if context else "#rows .row:not(.context)"
    return page.eval_on_selector_all(selector, "rows => rows.map(r => r.dataset.id)")


def row(page, key):
    return page.locator(f'#rows .row[data-id="{key}"]')


def polls(page, count):
    """Run ``count`` 5s poll cycles on an installed fake clock."""
    for _ in range(count):
        page.clock.run_for(5100)
        page.wait_for_timeout(250)


def queries(page):
    found = []
    page.on("request", lambda request: found.append(request.url) if "/api/" in request.url else None)
    return found


def open_menu_item(page, label):
    page.click("#more-actions")
    page.get_by_role("menuitem", name=label).click()


def discard_changes(page):
    page.locator("dialog[open]").get_by_role("button", name="Discard", exact=True).click()
    expect(page.locator("dialog[open]")).to_have_count(0)


def detail_key(page):
    return page.locator("#preview .d-key")


# ---- 1. density (P0-4) ----


def test_density_and_chrome(rw, desktop):
    for n in range(25):
        rw.api("/api/items", {"title": f"Density filler {n}", "type": "task", "project": "DIV"})
    empty = rw.api("/api/items", {"title": "Empty epic", "type": "epic", "project": "DIV"})["id"]
    # One project: no group headers, so the first row sits right under the 28px list header.
    visit(desktop, rw, "/?view=drafts&project=DIV")
    boxes = desktop.eval_on_selector_all("#rows .row", "rows => rows.map(r => r.getBoundingClientRect().toJSON())")
    assert boxes[0]["top"] <= 130
    assert sum(box["bottom"] < 900 for box in boxes) >= 20
    assert all(box["height"] <= 37 for box in boxes)
    expect(row(desktop, empty).locator(".progress")).to_have_text("")
    # All projects: the first project's group header takes that place, and rows keep their height.
    visit(desktop, rw, "/?view=backlog")
    assert desktop.eval_on_selector(".group-header", "g => g.getBoundingClientRect().top") <= 130
    heights = desktop.eval_on_selector_all("#rows .row", "rows => rows.map(r => r.offsetHeight)")
    assert max(heights) <= 37
    text = desktop.inner_text("body")
    assert "Apply filters" not in text and "Plan, follow through" not in text
    assert desktop.locator("#apply-filters").count() == 0

    visit(desktop, rw, "/?view=done")
    row(desktop, "TM-3").hover()
    assert desktop.eval_on_selector('.row[data-id="TM-3"] .handle', "h => getComputedStyle(h).visibility") == "hidden"
    visit(desktop, rw, "/?view=backlog")
    row(desktop, "RES-5").hover()
    assert desktop.eval_on_selector('.row[data-id="RES-5"] .handle', "h => getComputedStyle(h).visibility") == "visible"

    desktop.set_viewport_size({"width": 1180, "height": 800})
    visit(desktop, rw, "/?view=backlog&item=TM-8")
    expect(detail_key(desktop)).to_have_text("TM-8")
    heights = desktop.eval_on_selector_all("#rows .row", "rows => rows.map(r => r.offsetHeight)")
    assert max(heights) <= 37
    # Only owner avatars stay at this width; the header keeps a labelled icon.
    expect(desktop.locator(".columns .h-owner .h-owner-icon")).to_be_visible()
    expect(desktop.locator(".columns .h-owner")).to_have_attribute("title", "Owner")


# ---- 2. row content (P0-5) ----


def test_rows_show_keys_and_signals(ro, desktop):
    for view in ("attention", "active", "backlog", "done", "blocked"):
        visit(desktop, ro, f"/?view={view}")
        pairs = desktop.eval_on_selector_all("#rows .row", "rows => rows.map(r => [r.dataset.id, r.querySelector('.key').textContent])")
        assert pairs and all(key == text for key, text in pairs)

    visit(desktop, ro, "/?view=active")
    res4 = row(desktop, "RES-4")
    text = res4.inner_text()
    for word in ("RES-4", "aws", "bug", "Vlad"):
        assert word in text
    assert res4.locator("[data-verification=fail]").count() == 1
    expect(row(desktop, "TM-1").locator(".progress")).to_have_text("3/8")
    expect(row(desktop, "TM-6").locator(".progress")).to_have_text("0/4")
    assert "Pri." not in desktop.inner_text(".columns")
    row(desktop, "TM-7").locator("[data-state]").click()
    expect(detail_key(desktop)).to_have_text("TM-7")

    visit(desktop, ro, "/?view=blocked")
    tm10 = row(desktop, "TM-10").locator("[data-state]").text_content()
    assert "Blocked" in tm10 and "TM-8" in tm10 and "TM-9" in tm10
    tm8 = row(desktop, "TM-8")
    assert "Blocked · TM-7" in tm8.locator("[data-state]").text_content()
    assert "Codex" in tm8.text_content()
    # Two blockers fit the State column at full width; the title wins space over the breadcrumb.
    assert row(desktop, "TM-10").locator(".state").evaluate("s => s.scrollWidth <= s.clientWidth")
    tm9 = row(desktop, "TM-9").locator(".title")
    assert tm9.evaluate("t => t.scrollWidth <= t.clientWidth")
    # Agents get a glyph, not the interaction colour.
    avatar = tm8.locator(".avatar[data-agent]")
    assert avatar.evaluate("a => getComputedStyle(a).backgroundColor") != desktop.evaluate(
        """() => { const p = document.createElement('span'); p.style.background = 'var(--accent-soft)';
        document.body.append(p); const c = getComputedStyle(p).backgroundColor; p.remove(); return c; }""")

    visit(desktop, ro, "/?view=drafts")
    chip = row(desktop, "TM-12").locator(".chip-draft")
    assert chip.evaluate("c => getComputedStyle(c).borderTopStyle") == "dashed"


# ---- 3. views and counts (P0-7) ----


def test_views_and_counts(ro, desktop):
    visit(desktop, ro, "/")
    expect(desktop.locator("#view-title")).to_have_text("Attention")
    expect(desktop.locator("[data-view=done] .n-count")).to_have_text("6")
    counts = [desktop.inner_text(f"[data-view={view}] .n-count")
              for view in ("attention", "active", "ready", "blocked", "drafts", "backlog", "done")]
    assert counts == ["1", "7", "5", "4", "6", "15", "6"]
    assert row_ids(desktop) == ["RES-4"]
    assert desktop.locator("#hide-done").count() == 0

    desktop.click("[data-view=ready]")
    expect(desktop.locator("#view-title")).to_have_text("Ready")
    expect(desktop.locator("#count")).to_have_text("5 work items")
    assert set(row_ids(desktop)) == {"RES-5", "RES-7", "RES-9", "RES-11", "RES-13"}

    visit(desktop, ro, "/?view=blocked")
    assert set(row_ids(desktop)) == {"TM-8", "TM-9", "TM-10", "TM-11"}
    headers = desktop.eval_on_selector_all(".group-header", "hs => hs.map(h => h.textContent)")
    assert "TM-7 · unblocks 2" in headers

    visit(desktop, ro, "/?view=drafts")
    assert set(row_ids(desktop, context=False)) == {"TM-12", "TM-13", "RES-6", "RES-8", "RES-10", "RES-12"}

    expect(desktop.locator("#sort option[value=manual]")).to_have_text("Manual")
    visit(desktop, ro, "/?view=done")
    rows = [r for r in ro.api("/api/query", {"view": "done"})["rows"] if not r["context"]]
    newest_first = [r["id"] for r in sorted(rows, key=lambda r: r["changed_at"], reverse=True)]
    assert row_ids(desktop) == newest_first
    expect(desktop.locator("#sort option[value=manual]")).to_have_text("Newest first")


# ---- 4. projects (P0-6) ----


def test_projects(rw, desktop):
    visit(desktop, rw, "/?view=active")
    counts = {key: desktop.inner_text(f"[data-project={key}] .n-count") for key in ("TM", "RES", "DIV")}
    assert counts == {"TM": "9", "RES": "12", "DIV": "1"}
    groups = desktop.eval_on_selector_all(".group-header", "hs => hs.map(h => h.dataset.group)")
    assert groups == ["TM", "RES", "DIV"]

    desktop.click("[data-project=RES]")
    expect(desktop.locator("#scope")).to_have_text("· RES")
    assert "project=RES" in desktop.url
    expect(desktop.locator(".group-header")).to_have_count(0)
    assert all(key.startswith("RES-") for key in row_ids(desktop))

    desktop.click("#new")
    select = desktop.locator("dialog[open] select[name=project]")
    expect(select).to_have_value("RES")
    values = select.evaluate("s => [...s.options].map(o => o.value)")
    assert set(values) <= {"TM", "RES", "DIV", "__new", ""}

    desktop.keyboard.press("Escape")
    expect(desktop.locator("dialog[open]")).to_have_count(0)

    # A drag onto another project's row is not a drop target and sends nothing.
    seen = queries(desktop)
    visit(desktop, rw, "/?view=backlog")
    before = rw.api("/api/query", {})["rows"]
    roots = {project: [r["id"] for r in before if r["project"] == project and r["parent_id"] is None
                       and r["status"] != "done" and row(desktop, r["id"]).count()] for project in ("TM", "RES")}
    source, top = roots["RES"][-1], roots["RES"][0]
    assert source != top
    handle = f'.row[data-id="{source}"] .handle'
    row(desktop, source).hover()
    desktop.drag_and_drop(handle, f'.row[data-id="{roots["TM"][0]}"]')
    desktop.wait_for_timeout(500)
    assert not [url for url in seen if url.endswith("/api/reorder")]
    assert [r["order"] for r in rw.api("/api/query", {})["rows"]] == [r["order"] for r in before]

    # A drag within the project reorders, and the API keeps the order per project.
    with desktop.expect_response(lambda r: r.url.endswith("/api/reorder")) as info:
        row(desktop, source).hover()
        desktop.drag_and_drop(handle, f'.row[data-id="{top}"]', target_position={"x": 40, "y": 2})
    assert info.value.status == 200
    order = info.value.json()["order"]
    # Rows outside the view (RES-1 is active) keep their place; the source lands just above the target.
    assert order.index(source) == order.index(top) - 1 and all(key.startswith("RES-") for key in order)
    with pytest.raises(urllib.error.HTTPError) as error:
        rw.api("/api/reorder", {"id": source, "position": len(order) + 1})
    assert error.value.code == 400


# ---- 5. URL state (P0-3) ----


def test_url_state(ro, desktop):
    visit(desktop, ro, "/?item=TM-7")
    expect(detail_key(desktop)).to_have_text("TM-7")
    visit(desktop, ro, "/TM-7")
    expect(detail_key(desktop)).to_have_text("TM-7")
    assert desktop.url.endswith("/?item=TM-7")

    visit(desktop, ro, "/")
    row(desktop, "RES-4").click()
    expect(detail_key(desktop)).to_have_text("RES-4")
    assert "item=RES-4" in desktop.url
    desktop.go_back()
    expect(desktop.locator("#preview")).to_be_hidden()

    visit(desktop, ro, "/?view=done")
    expect(desktop.locator("#view-title")).to_have_text("Done")
    assert len(row_ids(desktop)) == 6

    visit(desktop, ro, "/?view=active")
    row(desktop, "TM-1").locator(".chev").click()
    expect(row(desktop, "TM-1")).to_have_attribute("aria-expanded", "false")
    desktop.reload()
    expect(row(desktop, "TM-1")).to_have_attribute("aria-expanded", "false")
    assert row(desktop, "TM-7").count() == 0

    visit(desktop, ro, "/RES-99")
    expect(desktop.locator("#preview [role=alert]")).to_contain_text("RES-99 was not found")


# ---- 6. detail (P0-8) ----

RAW = re.compile(r"null|\[\]|\[\"|T\d\d:\d\d:\d\d\.\d{6}|revision")


def test_detail_panel(ro, desktop):
    visit(desktop, ro, "/?view=blocked&item=TM-10")
    expect(detail_key(desktop)).to_have_text("TM-10")
    crumbs = desktop.eval_on_selector_all("#preview .crumbs a", "as => as.map(a => a.dataset.ref)")
    assert crumbs == ["TM-1", "TM-6"]
    deps = desktop.eval_on_selector_all('#preview section[aria-label="Depends on"] [data-ref]', "es => es.map(e => e.dataset.ref)")
    assert deps == ["TM-8", "TM-9"]
    desktop.click('#preview section[aria-label="Depends on"] [data-ref="TM-8"]')
    expect(detail_key(desktop)).to_have_text("TM-8")

    visit(desktop, ro, "/?item=TM-7")
    expect(detail_key(desktop)).to_have_text("TM-7")
    blocks = desktop.eval_on_selector_all('#preview section[aria-label="Blocks"] [data-ref]', "es => es.map(e => e.dataset.ref)")
    assert blocks == ["TM-8", "TM-9"]
    desktop.click("#more-actions")
    expect(desktop.get_by_role("menuitem", name="Add comment")).to_be_visible()
    assert desktop.get_by_role("menuitem", name=re.compile("Delete draft")).count() == 0
    desktop.keyboard.press("Escape")

    visit(desktop, ro, "/?item=TM-11")
    expect(detail_key(desktop)).to_have_text("TM-11")
    subs = desktop.eval_on_selector_all('#preview section[aria-label="Sub-items"] [data-ref]', "es => es.map(e => e.dataset.ref)")
    assert subs == ["TM-12"]
    deps = desktop.eval_on_selector_all('#preview section[aria-label="Depends on"] [data-ref]', "es => es.map(e => e.dataset.ref)")
    assert deps == ["TM-6"]

    for key in ("TM-6", "TM-7", "TM-10", "TM-12", "RES-4", "RES-5", "TM-3"):
        visit(desktop, ro, f"/?item={key}")
        expect(desktop.locator("#detail-title")).to_be_visible()
        text = desktop.inner_text("#preview")
        assert not RAW.search(text), (key, RAW.search(text))
        lines = desktop.eval_on_selector_all("#preview .description *", "es => es.map(e => e.textContent)")
        assert not any(line.lstrip().startswith("#") for line in lines)

    visit(desktop, ro, "/?item=RES-4")
    expect(desktop.locator("#preview .verdict")).to_contain_text("Not closable")
    status = desktop.locator('#preview .gate[data-check="verification"] .gate-status')
    expect(status).to_have_text("Fail")
    fail = desktop.evaluate("""() => { const p = document.createElement('span'); p.style.color = 'var(--fail)';
        document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c; }""")
    assert status.evaluate("s => getComputedStyle(s).color") == fail
    expect(desktop.locator('#preview .gate[data-check="summary"] .gate-status')).to_have_text("Missing")
    first = desktop.locator("#preview .timeline > *").first
    expect(first).to_have_attribute("data-result", "fail")
    entries = desktop.locator("#preview .timeline .entry")
    assert entries.count() > 2
    assert desktop.locator("#preview .timeline .entry time[datetime]").count() == entries.count()

    visit(desktop, ro, "/?item=TM-12")
    finalized = desktop.locator('#preview .gate[data-check="finalized"]')
    expect(finalized).to_have_text("Not finalized (draft)")
    assert finalized.locator("[data-ref]").count() == 0

    visit(desktop, ro, "/?item=TM-3")
    expect(desktop.locator("#preview .locked")).to_have_text("Closed with evidence")
    assert desktop.locator("#edit-item").count() == 0


# ---- 7. poll safety (P0-1) ----


def test_polling_keeps_focus_selection_and_nodes(ro, desktop):
    desktop.clock.install()
    seen = queries(desktop)
    visit(desktop, ro, "/?view=active&item=TM-6")
    expect(desktop.locator("#preview .description")).to_be_visible()
    desktop.evaluate("""() => {
        window.__row = document.querySelector('.row[data-id="TM-7"]');
        window.__mutations = 0;
        const observer = new MutationObserver(records => { window.__mutations += records.length; });
        for (const el of document.querySelectorAll('[role=status],[role=alert]'))
            observer.observe(el, {subtree: true, childList: true, characterData: true, attributes: true});
    }""")
    desktop.focus('.row[data-id="TM-7"]')
    selected = desktop.evaluate("""() => { const range = document.createRange();
        range.selectNodeContents(document.querySelector('#preview .description'));
        getSelection().removeAllRanges(); getSelection().addRange(range); return getSelection().toString().length; }""")
    assert selected > 0
    start = len(seen)
    polls(desktop, 6)
    assert len([url for url in seen[start:] if url.endswith("/api/query")]) >= 5
    assert desktop.evaluate("document.activeElement.closest('.row')?.dataset.id") == "TM-7"
    assert desktop.evaluate("getSelection().toString().length") == selected
    assert desktop.evaluate("window.__row.isConnected")
    assert desktop.evaluate("window.__mutations") == 0
    # Detail polls send the rendered etag, so an unchanged item costs no payload.
    shows = [url for url in seen[start:] if "/api/show/" in url]
    assert shows and all("etag=" in url for url in shows)

    desktop.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    start = len(seen)
    polls(desktop, 3)
    assert seen[start:] == []


def test_slow_server_keeps_one_query_in_flight(ro, desktop):
    desktop.add_init_script("""(() => {
        const real = window.fetch;
        window.__inflight = 0; window.__max = 0; window.__queries = 0;
        window.fetch = async (url, init) => {
            if (!String(url).includes('/api/query')) return real(url, init);
            window.__queries++; window.__inflight++;
            window.__max = Math.max(window.__max, window.__inflight);
            try { await new Promise(r => setTimeout(r, 7000)); return await real(url, init); }
            finally { window.__inflight--; }
        };
    })()""")
    desktop.goto(ro.base + "/")
    expect(row(desktop, "RES-4")).to_be_visible(timeout=8000)
    desktop.wait_for_function("window.__queries >= 2", timeout=10000)
    assert desktop.evaluate("window.__max") == 1


# ---- 8. integrity (P0-2) ----


def test_integrity(rw, desktop):
    before = len(rw.api("/api/query", {})["rows"])
    visit(desktop, rw, "/")
    desktop.click("#new")
    desktop.fill("dialog[open] input[name=title]", "Only once")
    desktop.select_option("dialog[open] select[name=project]", "TM")
    desktop.evaluate("""() => { const form = document.querySelector('dialog[open] form');
        form.requestSubmit(); form.requestSubmit(); form.requestSubmit(); }""")
    expect(desktop.locator("dialog[open]")).to_have_count(0)
    desktop.wait_for_timeout(500)
    assert len(rw.api("/api/query", {})["rows"]) == before + 1

    seen = queries(desktop)
    visit(desktop, rw, "/?item=RES-5")
    expect(detail_key(desktop)).to_have_text("RES-5")
    open_menu_item(desktop, "Assignee and labels")
    desktop.fill("dialog[open] input[name=assignee]", "bob")
    desktop.fill("dialog[open] input[aria-label='Add label']", "Bad Label")
    desktop.click("dialog[open] button[type=submit]")
    expect(desktop.locator("dialog[open] .field-error:visible")).to_contain_text("not a valid label")
    assert not [url for url in seen if url.endswith("/api/fields")]
    shown = rw.api("/api/show/RES-5")["frontmatter"]
    assert shown["assignee"] is None and shown["labels"] == ["bug", "ui"]
    desktop.keyboard.press("Escape")
    discard_changes(desktop)

    desktop.click("#edit-item")
    desktop.request.post(rw.base + "/api/edit", headers=HEADERS, data=json.dumps(
        {"id": "RES-5", "title": "Show onboarding wizard validation errors", "body": "Other body\n", "reason": "Other edit"}))
    actor = rw.api("/api/query", {"view": "attention"})["actor"]
    desktop.fill("dialog[open] textarea[name=body]", "My body\n")
    desktop.fill("dialog[open] input[name=reason]", "Mine")
    desktop.click("dialog[open] button[type=submit]")
    alert = desktop.locator("dialog[open] [role=alert]")
    expect(alert).to_contain_text(f"Changed by {actor}")
    expect(desktop.locator("dialog[open]")).to_have_count(1)
    assert rw.api("/api/show/RES-5")["body"] == "Other body\n"
    desktop.keyboard.press("Escape")
    discard_changes(desktop)

    # A child's change bumps the parent's revision; the parent edit still saves.
    visit(desktop, rw, "/?item=TM-6")
    expect(detail_key(desktop)).to_have_text("TM-6")
    desktop.click("#edit-item")
    rw.api("/api/comment", {"id": "TM-7", "kind": "progress", "text": "Child moved on"})
    desktop.fill("dialog[open] input[name=title]", "Serve Taskman over HTTP")
    desktop.fill("dialog[open] input[name=reason]", "Shorter title")
    desktop.click("dialog[open] button[type=submit]")
    expect(desktop.locator("dialog[open]")).to_have_count(0)
    assert rw.api("/api/show/TM-6")["frontmatter"]["title"] == "Serve Taskman over HTTP"


# ---- 9. filters and sort (P0-9) ----


def test_filters_and_sort(ro, desktop):
    desktop.clock.install()
    visit(desktop, ro, "/?view=active")
    desktop.click("[data-facet=label]")
    desktop.click(".popover label:has-text('backend')")
    expect(desktop.locator("#count")).to_have_text("1 work item")
    expect(desktop.locator("#applied")).to_contain_text("Label is backend")
    text = desktop.inner_text("body")
    assert "Apply filters" not in text and "comma" not in text.lower()
    labels = desktop.eval_on_selector_all("#sort option", "os => os.map(o => o.textContent)")
    assert not any("_" in label or "journal" in label or "revision" in label for label in labels)
    polls(desktop, 2)
    assert desktop.eval_on_selector_all("#sort option", "os => os.map(o => o.textContent)") == labels

    desktop.keyboard.press("Escape")
    desktop.fill("#search", "zzz")
    desktop.clock.run_for(400)
    expect(desktop.locator("#empty")).to_contain_text("No matching work")
    desktop.click("#empty [data-clear]")
    expect(desktop.locator("#rows .row").first).to_be_visible()
    expect(desktop.locator("#search")).to_have_value("")
    assert desktop.locator("#applied .fchip").count() == 0

    desktop.fill("#search", "TM-10")
    desktop.press("#search", "Enter")
    expect(detail_key(desktop)).to_have_text("TM-10")


# ---- 10. keyboard (P0-11) ----


def test_keyboard(ro, desktop):
    visit(desktop, ro, "/?view=active")
    assert desktop.get_attribute("#tree", "role") == "treegrid"
    desktop.focus("#search")
    desktop.keyboard.press("Tab")
    assert desktop.evaluate("document.activeElement.classList.contains('row')")
    third = row_ids(desktop)[2]
    desktop.keyboard.press("ArrowDown")
    desktop.keyboard.press("ArrowDown")
    desktop.keyboard.press("Enter")
    expect(detail_key(desktop)).to_have_text(third)
    expect(desktop.locator("#detail-title")).to_be_focused()
    fourth = row_ids(desktop)[3]
    desktop.keyboard.press("j")
    expect(detail_key(desktop)).to_have_text(fourth)
    desktop.keyboard.press("Escape")
    expect(desktop.locator("#preview")).to_be_hidden()
    assert desktop.evaluate("document.activeElement.dataset.id") == fourth
    desktop.keyboard.press("/")
    expect(desktop.locator("#search")).to_be_focused()
    desktop.keyboard.press("Escape")

    row(desktop, "TM-6").focus()
    desktop.keyboard.press("c")
    expect(desktop.locator("dialog[open] select[name=parent]")).to_have_value("TM-6")
    name = desktop.evaluate("""() => { const d = document.querySelector('dialog[open]');
        return document.getElementById(d.getAttribute('aria-labelledby')).textContent; }""")
    assert name == "Create task"
    desktop.keyboard.press("Escape")
    expect(desktop.locator("dialog[open]")).to_have_count(0)
    row(desktop, "TM-6").focus()
    desktop.keyboard.press("?")
    sheet = desktop.get_by_role("dialog", name="Keyboard shortcuts")
    expect(sheet).to_be_visible()
    expect(sheet.get_by_role("button", name="Cancel")).to_be_hidden()
    desktop.keyboard.press("Escape")

    # Every dialog the detail panel opens has an accessible name.
    visit(desktop, ro, "/?item=RES-5")
    expect(detail_key(desktop)).to_have_text("RES-5")
    for opener in ("#edit-item", "Add comment", "Assignee and labels"):
        if opener.startswith("#"):
            desktop.click(opener)
        else:
            open_menu_item(desktop, opener)
        expect(desktop.locator("dialog[open]")).to_have_accessible_name(re.compile(r"\S"))
        desktop.keyboard.press("Escape")
        expect(desktop.locator("dialog[open]")).to_have_count(0)


# ---- 11. create and comment (P0-14) ----


def test_create_and_comment(rw, desktop):
    visit(desktop, rw, "/?item=TM-6")
    desktop.click("#add-sub-item")
    expect(desktop.locator("dialog[open] input[name=title]")).to_be_focused()
    with desktop.expect_request(lambda r: r.url.endswith("/api/items") and r.method == "POST") as info:
        desktop.keyboard.type("X")
        desktop.keyboard.press("Enter")
    assert json.loads(info.value.post_data)["parent"] == "TM-6"
    new_id = info.value.response().json()["id"]
    expect(row(desktop, new_id)).to_be_in_viewport()
    expect(desktop.locator("#toasts")).to_contain_text(f"{new_id} created as draft")

    desktop.click("#new")
    parent = desktop.locator("dialog[open] select[name=parent]")
    expect(parent.locator("optgroup option").first).to_be_attached()
    options = parent.evaluate("s => [...s.options].filter(o => o.value).map(o => o.textContent)")
    assert options and all(re.match(r"^[A-Z][A-Z0-9]*-\d+ · ", text) for text in options)
    desktop.click("dialog[open] .pill:has-text('Epic')")
    expect(desktop.locator("dialog[open] h2")).to_have_text("Create epic")
    expect(desktop.locator("dialog[open] button[type=submit]")).to_have_text("Create epic")
    desktop.keyboard.press("Escape")
    discard_changes(desktop)

    seen = queries(desktop)
    visit(desktop, rw, "/?item=RES-5")
    open_menu_item(desktop, "Add comment")
    expect(desktop.locator("dialog[open] textarea[name=text]")).to_be_focused()
    expect(desktop.locator("dialog[open] select[name=kind]")).to_have_value("progress")
    desktop.select_option("dialog[open] select[name=kind]", "verification")
    desktop.fill("dialog[open] textarea[name=text]", "Checked by hand")
    desktop.keyboard.press("Control+Enter")
    expect(desktop.locator("dialog[open] .field-error:visible")).to_have_text("Choose the verification result")
    assert not [url for url in seen if url.endswith("/api/comment")]


# ---- 12. errors and connection (P0-13) ----


def test_errors_and_recovery(rw, desktop):
    desktop.route("**/api/query", lambda route: route.abort())
    desktop.goto(rw.base + "/")
    retry = desktop.locator("#empty button", has_text="Retry")
    expect(retry).to_be_visible()
    expect(desktop.locator("#message")).to_contain_text("Taskman server is not reachable")
    desktop.unroute("**/api/query")
    retry.click()
    expect(row(desktop, "RES-4")).to_be_visible()
    expect(desktop.locator(".message.error").first).to_be_hidden()
    expect(desktop.locator(".brand [data-conn]")).to_have_attribute("data-status", "live")

    visit(desktop, rw, "/?view=drafts&item=TM-13")
    expect(detail_key(desktop)).to_have_text("TM-13")
    expect(desktop.locator("#edit-item")).to_be_enabled()
    desktop.request.post(rw.base + "/api/discard", headers=HEADERS, data=json.dumps({"id": "TM-13"}))
    expect(desktop.locator("#preview [role=alert]")).to_have_text("TM-13 was deleted or discarded", timeout=7000)
    expect(desktop.locator("#edit-item")).to_be_disabled()
    # Only the title and the disabled actions stay; gates and timeline no longer describe anything.
    expect(desktop.locator("#detail-title")).to_be_visible()
    for stale in (".readiness", 'section[aria-label="Timeline"]', 'section[aria-label="Description"]'):
        assert desktop.locator(f"#preview {stale}").count() == 0, stale

    # Deleting a draft from its menu moves focus to the next row.
    visit(desktop, rw, "/?view=drafts&item=RES-10")
    expect(detail_key(desktop)).to_have_text("RES-10")
    ids = row_ids(desktop)
    neighbour = ids[ids.index("RES-10") + 1]
    open_menu_item(desktop, "Delete draft")
    desktop.locator("dialog[open] button[type=submit]").click()
    expect(row(desktop, "RES-10")).to_have_count(0)
    expect(row(desktop, neighbour)).to_be_focused()


def test_offline_indicator_keeps_its_place(ro, desktop):
    desktop.clock.install()
    visit(desktop, ro, "/?view=active")
    nav = desktop.eval_on_selector(".sidebar nav", "n => n.getBoundingClientRect().top")
    desktop.route("**/api/**", lambda route: route.abort())
    for _ in range(12):
        desktop.clock.run_for(10000)
        desktop.wait_for_timeout(50)
    conn = desktop.locator(".brand [data-conn]")
    expect(conn).to_have_attribute("data-status", "offline")
    expect(conn).to_have_attribute("title", re.compile(r"^Taskman server is not reachable\. Offline since .* showing last data$"))
    assert conn.evaluate("c => c.getBoundingClientRect().height") <= 20
    assert desktop.eval_on_selector(".sidebar nav", "n => n.getBoundingClientRect().top") == nav
    # The banner names the cause once; Retry lives in the indicator.
    expect(desktop.locator("#message")).to_have_text("Taskman server is not reachable")
    assert desktop.locator("#message button").count() == 0
    desktop.unroute("**/api/**")
    conn.get_by_role("button", name="Retry").click()
    expect(conn).to_have_attribute("data-status", "live")
    expect(desktop.locator("#message")).to_be_hidden()


# ---- 13. mobile (P0-10) ----


def test_mobile_layout(ro, mobile):
    visit(mobile, ro, "/?view=backlog")
    first = mobile.eval_on_selector("#rows .row", "r => r.getBoundingClientRect().top")
    assert first <= 200
    assert set(mobile.eval_on_selector_all("#rows .row", "rs => rs.map(r => r.offsetHeight)")) == {52}
    assert mobile.eval_on_selector("#new-m", "b => innerWidth - b.getBoundingClientRect().right") <= 16
    assert mobile.evaluate("document.documentElement.scrollWidth") == 390
    expect(mobile.locator(".topbar [data-conn]")).to_be_visible()
    assert mobile.eval_on_selector_all("[data-drag]", "hs => hs.filter(h => h.offsetParent && getComputedStyle(h).visibility !== 'hidden').length") == 0
    target = row(mobile, "RES-11")
    target.scroll_into_view_if_needed()
    scroll = mobile.evaluate("scrollY")
    assert scroll > 0
    target.tap()
    expect(detail_key(mobile)).to_have_text("RES-11")
    box = mobile.eval_on_selector("#preview", "p => p.getBoundingClientRect().toJSON()")
    assert box["top"] == 0 and box["height"] >= 800
    expect(mobile.locator("#detail-title")).to_be_focused()
    mobile.go_back()
    expect(mobile.locator("#preview")).to_be_hidden()
    assert mobile.evaluate("scrollY") == scroll


# ---- 14. theme and contrast (P0-12) ----

CONTRAST = """() => {
  const parse = (c) => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null;
    const p = m[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const channel = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const blend = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const background = (el) => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let color = layers.length && layers[layers.length - 1].a >= 1 ? layers.pop() : parse(getComputedStyle(document.body).backgroundColor);
    while (layers.length) color = blend(layers.pop(), color);
    return color;
  };
  const failures = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node.parentElement;
    if (!node.textContent.trim() || !el || el.closest("script,style,select,option,:disabled")) continue;
    const range = document.createRange(); range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) continue;
    const style = getComputedStyle(el);
    if (style.visibility !== "visible") continue;
    const bg = background(el);
    let fg = parse(style.color); if (fg.a < 1) fg = blend(fg, bg);
    const value = ratio(fg, bg);
    if (value < 4.5) failures.push([node.textContent.trim().slice(0, 30), Math.round(value * 100) / 100]);
  }
  const probe = (name) => { const s = document.createElement("span"); s.style.color = `var(${name})`;
    document.body.append(s); const c = parse(getComputedStyle(s).color); s.remove(); return c; };
  return { failures, focus: ratio(probe("--accent"), probe("--surface")),
           input: ratio(probe("--border-input"), probe("--surface")) };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_theme_and_contrast(ro, chromium, scheme):
    context, page = _page(chromium, DESKTOP, scheme)
    try:
        for path in ("/?view=active&item=RES-4", "/?view=backlog&item=TM-11", "/?view=done"):
            visit(page, ro, path)
            page.wait_for_timeout(300)
            result = page.evaluate(CONTRAST)
            assert result["failures"] == [], (path, result["failures"])
            assert result["focus"] >= 3 and result["input"] >= 3
        if scheme == "dark":
            assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(15, 17, 21)"
            assert page.eval_on_selector("#sort", "s => getComputedStyle(s).colorScheme") == "dark"
        assert not page.errors
    finally:
        context.close()


# ---- 16. escaping ----


def test_item_text_never_becomes_markup(rw, desktop):
    evil = "<img src=x onerror=alert(1)>"
    key = rw.api("/api/items", {"title": evil, "type": "feat", "project": "TM", "body": evil})["id"]
    rw.api("/api/fields", {"id": key, "assignee": evil, "labels": []})
    dialogs = []
    desktop.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    visit(desktop, rw, f"/?view=drafts&item={key}")
    expect(row(desktop, key).locator(".title")).to_have_text(evil)
    expect(desktop.locator("#detail-title")).to_have_text(evil)
    expect(row(desktop, key).locator(".owner-name")).to_have_text(evil)
    desktop.click("[data-facet=assignee]")
    expect(desktop.locator(".popover")).to_contain_text(evil)
    desktop.keyboard.press("Escape")
    desktop.click("#new")
    parent = desktop.locator(f"dialog[open] select[name=parent] option[value='{key}']")
    expect(parent).to_have_text(f"{key} · {evil}")
    desktop.fill("dialog[open] input[name=title]", evil)
    desktop.click("dialog[open] button[type=submit]")
    expect(desktop.locator("#toasts")).to_contain_text("created as draft")
    assert desktop.locator("img").count() == 0
    assert dialogs == []
