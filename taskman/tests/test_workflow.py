"""Lifecycle and HTTP acceptance with fresh clients sharing only persisted state."""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.ui import _make_handler
from taskman.storage.sql_store import open_sql_store


def cli(*args, text=None, ok=True):
    r = CliRunner().invoke(app, list(args), input=text)
    if ok:
        assert r.exit_code == 0, r.output
    else:
        assert r.exit_code != 0, r.output
    return r.output


def new(title, kind="task", parent=None):
    args = ["new", "--title", title, "--type", kind, "--body-file", "-", "--json"]
    args += ["--parent", parent] if parent else ["--project", "TM"]
    return json.loads(cli(*args, text=f"## Acceptance criteria\n{title} works locally.\n"))["id"]


def record(item, kind, text="Verified observable local behavior.", result=None, actor="Codex"):
    args = ["record", item, "--actor", actor, "--kind", kind, "--body-file", "-"]
    if result:
        args += ["--result", result]
    cli(*args, text=text)


def complete(item):
    record(item, "summary")
    record(item, "verification", result="pass", actor="Claude Code")
    cli("close", item)


@contextmanager
def server(settings):
    store = open_sql_store(settings, actor="UI")
    http = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(store))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
        store.close()


def get(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def post(url, data, header=True):
    request = Request(url, json.dumps(data).encode(), headers={"Content-Type": "application/json", **({"X-Taskman": "1"} if header else {})})
    with urlopen(request, timeout=3) as response:
        assert response.status == 201
        return json.load(response)


def test_ui_create_to_nested_plan_execution_and_fresh_agent_context(sqlite_env):
    with server(sqlite_env) as url:
        epic = post(url + "/api/items", {"title": "Local delivery", "type": "epic", "project": "TM", "body": "## Acceptance criteria\nA complete local workflow."})["id"]
        story = new("Story", "feat", epic)
        first = new("Implement", parent=story)
        second = new("Review", parent=story)
        cli("depend", second, "--on", first)
        for item in (epic, story, first, second):
            cli("finalize", item)
        cli("move", second, "active", ok=False)
        cli("move", first, "active")
        record(first, "decision", "Reuse the existing public contract.")
        complete(first)
        cli("move", second, "active")
        # Separate process: no CliRunner globals or conversation memory.
        result = subprocess.run([sys.executable, "-m", "taskman.cli", "context", second],
                                env={**os.environ, "TASKMAN_BACKEND": "sqlite", "TASKMAN_DATABASE_URL": f"sqlite://{sqlite_env.sqlite_path}"}, capture_output=True, text=True, check=True)
        context = json.loads(result.stdout)
        assert [a["id"] for a in context["ancestors"]] == [story, epic]
        assert context["dependencies"][0]["frontmatter"]["journal"][0]["text"] == "Reuse the existing public contract."
        complete(second)
        complete(story)
        complete(epic)
        cli("validate")
        page = get(url + "/api/page")
        assert page["sections"].index(epic) < page["sections"].index(story) < page["sections"].index(first)
        assert '<ul><li>' in page["sections"]
        shown = get(url + "/api/show/" + second)["frontmatter"]
        assert shown["status"] == "done"
        assert shown["draft"] is False
        assert shown["depends_on"] == [first]
        assert shown["journal"][-1]["actor"] == "Claude Code"


def test_close_requires_current_success_and_summary_and_cannot_be_bypassed(sqlite_env):
    item = new("Check me")
    cli("close", item, ok=False)
    cli("finalize", item)
    record(item, "verification", result="pass")
    cli("close", item, ok=False)  # no summary
    record(item, "summary")
    record(item, "verification", result="fail")
    cli("move", item, "done", ok=False)
    record(item, "verification", result="pass")
    cli("update", item, "--body-file", "-", text="Changed acceptance criteria")
    cli("close", item, ok=False)
    complete(item)
    frozen = json.loads(cli("show", item, "--json"))
    for args in (("move", item, "active"), ("convert", item, "feat"),
                 ("update", item, "--body-file", "-"), ("depend", item, "--clear"),
                 ("record", item, "--kind", "progress", "--actor", "Codex", "--body-file", "-")):
        cli(*args, text="Must not modify a closed task.", ok=False)
    assert json.loads(cli("show", item, "--json")) == frozen


def test_parent_evidence_becomes_stale_after_child_changes(sqlite_env):
    parent = new("Parent", "epic")
    child = new("Child", parent=parent)
    for item in (parent, child):
        cli("finalize", item)
    record(parent, "summary")
    record(parent, "verification", result="pass")
    complete(child)
    cli("close", parent, ok=False)
    complete(parent)
    cli("move", child, "active", ok=False)
    cli("new", "--title", "Late child", "--parent", parent, ok=False)


def test_dependency_cycles_rejected_without_writes(sqlite_env):
    parent = new("Parent", "epic")
    a = new("A", parent=parent)
    b = new("B", parent=parent)
    cli("depend", a, "--on", b)
    for dependency in (a, parent, "TM-99"):
        cli("depend", b, "--on", dependency, ok=False)
    assert not json.loads(cli("show", b, "--json"))["frontmatter"].get("depends_on")
    cli("depend", a, "--clear")
    cli("validate")


def test_ready_respects_drafts_and_parent_dependencies(sqlite_env):
    dep = new("Dependency")
    parent = new("Parent", "epic")
    child = new("Child", parent=parent)
    assert json.loads(cli("ready", "--json")) == []
    for item in (dep, parent, child):
        cli("finalize", item)
    cli("depend", parent, "--on", dep)
    assert {i["id"] for i in json.loads(cli("ready", "--json"))} == {dep}
    cli("move", child, "active", ok=False)
    complete(dep)
    assert child in {i["id"] for i in json.loads(cli("ready", "--json"))}


def test_discard_only_unreferenced_draft_leaves(sqlite_env):
    a, b = new("A"), new("B")
    cli("depend", b, "--on", a)
    cli("discard", a, ok=False)
    cli("discard", b)
    cli("finalize", a)
    cli("discard", a, ok=False)


def test_concurrent_ui_creation_unique_ids_and_invalid_requests(sqlite_env):
    with server(sqlite_env) as url:
        with ThreadPoolExecutor(max_workers=5) as pool:
            items = list(pool.map(lambda n: post(url + "/api/items", {"title": f"Task {n}", "project": "TM"}), range(8)))
        assert len({i["id"] for i in items}) == 8
        for data in ({"title": ""}, {"title": "Bad type", "type": "anything"}, {"title": "Missing parent", "parent": "TM-99"}):
            with pytest.raises(HTTPError) as err:
                post(url + "/api/items", data)
            assert err.value.code == 400
        with pytest.raises(HTTPError) as err:
            post(url + "/api/items", {"title": "Cross-origin write"}, header=False)
        assert err.value.code == 403
    cli("validate")


def test_type_conversion_invalidates_item_and_parent_evidence(sqlite_env):
    parent = new("Parent", "epic")
    item = new("Item", parent=parent)
    for value in (parent, item):
        cli("finalize", value)
        record(value, "summary")
        record(value, "verification", result="pass")
    before = json.loads(cli("context", item))
    cli("convert", item, "feat")
    after = json.loads(cli("context", item))
    assert after["item"]["frontmatter"]["revision"] == before["item"]["frontmatter"].get("revision", 0) + 1
    assert after["ancestors"][0]["frontmatter"]["revision"] == before["ancestors"][0]["frontmatter"]["revision"] + 1
    cli("close", item, ok=False)


def test_cannot_close_through_unfinished_parent_dependency(sqlite_env):
    dependency = new("Dependency")
    parent = new("Parent", "epic")
    child = new("Child", parent=parent)
    for item in (dependency, parent, child):
        cli("finalize", item)
    cli("depend", parent, "--on", dependency)
    assert [v["id"] for v in json.loads(cli("waiting-on", child, "--json"))] == [dependency]
    record(child, "summary")
    record(child, "verification", result="pass")
    cli("close", child, ok=False)
    complete(dependency)
    cli("close", child)
