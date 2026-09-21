"""Lifecycle and HTTP acceptance with fresh clients sharing only persisted state."""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.ui import _make_handler


def cli(root, *args, text=None, ok=True):
    r = CliRunner().invoke(app, list(args), env={"TASKMAN_TASKS_DIR": str(root)}, input=text)
    if ok:
        assert r.exit_code == 0, r.output
    else:
        assert r.exit_code != 0, r.output
    return r.output


def new(root, title, kind="task", parent=None):
    args = ["new", "--title", title, "--type", kind, "--body-file", "-", "--json"]
    if parent:
        args += ["--parent", parent]
    return json.loads(cli(root, *args, text=f"## Acceptance criteria\n{title} works locally.\n"))["id"]


def record(root, item, kind, text="Verified observable local behavior.", result=None, actor="Codex"):
    args = ["record", item, "--actor", actor, "--kind", kind, "--body-file", "-"]
    if result:
        args += ["--result", result]
    cli(root, *args, text=text)


def complete(root, item):
    record(root, item, "summary")
    record(root, item, "verification", result="pass", actor="Claude Code")
    cli(root, "close", item)


@contextmanager
def server(root):
    http = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def get(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def post(url, data, header=True):
    request = Request(url, json.dumps(data).encode(), headers={"Content-Type": "application/json", **({"X-Taskman": "1"} if header else {})})
    with urlopen(request, timeout=3) as response:
        assert response.status == 201
        return json.load(response)


def test_ui_create_to_nested_plan_execution_and_fresh_agent_context(tmp_path):
    with server(tmp_path) as url:
        epic = post(url + "/api/items", {"title": "Local delivery", "type": "epic", "body": "## Acceptance criteria\nA complete local workflow."})["id"]
        story = new(tmp_path, "Story", "feat", epic)
        first = new(tmp_path, "Implement", parent=story)
        second = new(tmp_path, "Review", parent=story)
        cli(tmp_path, "depend", second, "--on", first)
        for item in (epic, story, first, second):
            cli(tmp_path, "finalize", item)
        cli(tmp_path, "move", second, "active", ok=False)
        cli(tmp_path, "move", first, "active")
        record(tmp_path, first, "decision", "Reuse the existing public contract.")
        complete(tmp_path, first)
        cli(tmp_path, "move", second, "active")
        # Separate process: no CliRunner globals or conversation memory.
        result = subprocess.run([sys.executable, "-m", "taskman.cli", "context", second],
                                env={**os.environ, "TASKMAN_TASKS_DIR": str(tmp_path)}, capture_output=True, text=True, check=True)
        context = json.loads(result.stdout)
        assert [a["id"] for a in context["ancestors"]] == [story, epic]
        assert context["dependencies"][0]["frontmatter"]["journal"][0]["text"] == "Reuse the existing public contract."
        complete(tmp_path, second)
        complete(tmp_path, story)
        complete(tmp_path, epic)
        cli(tmp_path, "validate")
        page = get(url + "/api/page")
        assert page["sections"].index(epic) < page["sections"].index(story) < page["sections"].index(first)
        assert '<ul><li>' in page["sections"]
        shown = get(url + "/api/show/" + second)["frontmatter"]
        assert shown["status"] == "done"
        assert shown["draft"] is False
        assert shown["depends_on"] == [first]
        assert shown["journal"][-1]["actor"] == "Claude Code"


def test_close_requires_current_success_and_summary_and_cannot_be_bypassed(tmp_path):
    item = new(tmp_path, "Check me")
    cli(tmp_path, "close", item, ok=False)
    cli(tmp_path, "finalize", item)
    record(tmp_path, item, "verification", result="pass")
    cli(tmp_path, "close", item, ok=False)  # no summary
    record(tmp_path, item, "summary")
    record(tmp_path, item, "verification", result="fail")
    cli(tmp_path, "move", item, "done", ok=False)
    record(tmp_path, item, "verification", result="pass")
    cli(tmp_path, "update", item, "--body-file", "-", text="Changed acceptance criteria")
    cli(tmp_path, "close", item, ok=False)
    complete(tmp_path, item)
    frozen = json.loads(cli(tmp_path, "show", item, "--json"))
    for args in (("move", item, "active"), ("convert", item, "feat"),
                 ("update", item, "--body-file", "-"), ("depend", item, "--clear"),
                 ("record", item, "--kind", "progress", "--actor", "Codex", "--body-file", "-")):
        cli(tmp_path, *args, text="Must not modify a closed task.", ok=False)
    assert json.loads(cli(tmp_path, "show", item, "--json")) == frozen


def test_parent_evidence_becomes_stale_after_child_changes(tmp_path):
    parent = new(tmp_path, "Parent", "epic")
    child = new(tmp_path, "Child", parent=parent)
    for item in (parent, child):
        cli(tmp_path, "finalize", item)
    record(tmp_path, parent, "summary")
    record(tmp_path, parent, "verification", result="pass")
    complete(tmp_path, child)
    cli(tmp_path, "close", parent, ok=False)
    complete(tmp_path, parent)
    cli(tmp_path, "move", child, "active", ok=False)
    cli(tmp_path, "new", "--title", "Late child", "--parent", parent, ok=False)


def test_dependency_cycles_rejected_without_writes(tmp_path):
    parent = new(tmp_path, "Parent", "epic")
    a = new(tmp_path, "A", parent=parent)
    b = new(tmp_path, "B", parent=parent)
    cli(tmp_path, "depend", a, "--on", b)
    for dependency in (a, parent, "999999999"):
        cli(tmp_path, "depend", b, "--on", dependency, ok=False)
    assert not json.loads(cli(tmp_path, "show", b, "--json"))["frontmatter"].get("depends_on")
    cli(tmp_path, "depend", a, "--clear")
    cli(tmp_path, "validate")


def test_ready_respects_drafts_and_parent_dependencies(tmp_path):
    dep = new(tmp_path, "Dependency")
    parent = new(tmp_path, "Parent", "epic")
    child = new(tmp_path, "Child", parent=parent)
    assert json.loads(cli(tmp_path, "ready", "--json")) == []
    for item in (dep, parent, child):
        cli(tmp_path, "finalize", item)
    cli(tmp_path, "depend", parent, "--on", dep)
    assert {i["id"] for i in json.loads(cli(tmp_path, "ready", "--json"))} == {dep}
    cli(tmp_path, "move", child, "active", ok=False)
    complete(tmp_path, dep)
    assert child in {i["id"] for i in json.loads(cli(tmp_path, "ready", "--json"))}


def test_discard_only_unreferenced_draft_leaves(tmp_path):
    a, b = new(tmp_path, "A"), new(tmp_path, "B")
    cli(tmp_path, "depend", b, "--on", a)
    cli(tmp_path, "discard", a, ok=False)
    cli(tmp_path, "discard", b)
    cli(tmp_path, "finalize", a)
    cli(tmp_path, "discard", a, ok=False)


def test_concurrent_ui_creation_unique_ids_and_invalid_requests(tmp_path):
    with server(tmp_path) as url:
        with ThreadPoolExecutor(max_workers=5) as pool:
            items = list(pool.map(lambda n: post(url + "/api/items", {"title": f"Task {n}"}), range(8)))
        assert len({i["id"] for i in items}) == 8
        for data in ({"title": ""}, {"title": "Bad type", "type": "anything"}, {"title": "Missing parent", "parent": "999999999"}):
            with pytest.raises(HTTPError) as err:
                post(url + "/api/items", data)
            assert err.value.code == 400
        with pytest.raises(HTTPError) as err:
            post(url + "/api/items", {"title": "Cross-origin write"}, header=False)
        assert err.value.code == 403
    cli(tmp_path, "validate")


def test_type_conversion_invalidates_item_and_parent_evidence(tmp_path):
    parent = new(tmp_path, "Parent", "epic")
    item = new(tmp_path, "Item", parent=parent)
    for value in (parent, item):
        cli(tmp_path, "finalize", value)
        record(tmp_path, value, "summary")
        record(tmp_path, value, "verification", result="pass")
    before = json.loads(cli(tmp_path, "context", item))
    cli(tmp_path, "convert", item, "feat")
    after = json.loads(cli(tmp_path, "context", item))
    assert after["item"]["frontmatter"]["revision"] == before["item"]["frontmatter"].get("revision", 0) + 1
    assert after["ancestors"][0]["frontmatter"]["revision"] == before["ancestors"][0]["frontmatter"]["revision"] + 1
    cli(tmp_path, "close", item, ok=False)


def test_cannot_close_through_unfinished_parent_dependency(tmp_path):
    dependency = new(tmp_path, "Dependency")
    parent = new(tmp_path, "Parent", "epic")
    child = new(tmp_path, "Child", parent=parent)
    for item in (dependency, parent, child):
        cli(tmp_path, "finalize", item)
    cli(tmp_path, "depend", parent, "--on", dependency)
    assert [v["id"] for v in json.loads(cli(tmp_path, "waiting-on", child, "--json"))] == [dependency]
    record(tmp_path, child, "summary")
    record(tmp_path, child, "verification", result="pass")
    cli(tmp_path, "close", child, ok=False)
    complete(tmp_path, dependency)
    cli(tmp_path, "close", child)


def test_find_project_tasks_from_nested_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_TASKS_DIR", raising=False)
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    from taskman.commands.new import get_tasks_dir
    assert get_tasks_dir() == tmp_path / "tasks"
    (tmp_path / "tasks").mkdir()
    assert get_tasks_dir() == tmp_path / "tasks"
