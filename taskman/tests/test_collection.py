import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from taskman.cli import app
from taskman.model.collection import collection, query, consolidate, histories
from taskman.commands.collection import edit_item
from taskman.model.layout import find_item_by_id
from taskman.model.yaml_io import read_file, write_file
from tests.workflow_helpers import prepare

runner = CliRunner()


def new(tree, title, kind="task", parent=None):
    args = ["new", "--title", title, "--type", kind, "--json"]
    if parent:
        args += ["--parent", parent]
    result = runner.invoke(app, args, env={"TASKMAN_TASKS_DIR": str(tree)})
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["id"]


def close(tree, id):
    prepare(tree, id)
    result = runner.invoke(app, ["close", id], env={"TASKMAN_TASKS_DIR": str(tree)})
    assert result.exit_code == 0, result.output


def test_combined_query_context_sort_and_every_metadata_key(tmp_path):
    tree = tmp_path / "tasks"
    epic = new(tree, "Epic", "epic")
    a = new(tree, "Zebra", parent=epic)
    b = new(tree, "Alpha", parent=epic)
    path = find_item_by_id(tree, a)
    meta, body = read_file(path)
    meta["custom"] = {"region": "West"}
    meta["nullable"] = None
    meta["labels"] = ["one", "two"]
    write_file(path, meta, "Needle")
    rows = collection(tree)
    result = query(
        rows,
        [
            {"field": "custom", "op": "contains", "value": "West"},
            {"field": "labels", "op": "eq", "value": "two"},
        ],
        keyword="needle",
        epic=epic,
    )
    assert [r["id"] for r in result] == [epic, a]
    assert result[0]["context"] and not result[1]["context"]
    assert [r["id"] for r in query(rows, sort="title")] == [epic, b, a]
    assert any(
        r["id"] == a and not r["context"]
        for r in query(rows, [{"field": "nullable", "op": "exists"}])
    )
    for key, value in meta.items():
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        assert any(
            r["id"] == a and not r["context"]
            for r in query(rows, [{"field": key, "op": "contains", "value": text}])
        )
    result = runner.invoke(
        app,
        ["browse", "--contains", "custom=West", "--epic", epic, "--keyword", "needle"],
        env={"TASKMAN_TASKS_DIR": str(tree)},
    )
    assert result.exit_code == 0, result.output
    assert [r["id"] for r in json.loads(result.output)] == [epic, a]


def test_edit_and_delete_preserve_closed_and_referenced_work(tmp_path):
    tree = tmp_path / "tasks"
    a = new(tree, "Original")
    b = new(tree, "Reference")
    edit_item(tree, a, "Changed", "Body", "Clarify the requirement")
    meta, body = read_file(find_item_by_id(tree, a))
    assert meta["title"] == "Changed" and body == "Body"
    assert meta["journal"][-1]["text"] == "Clarify the requirement"
    result = runner.invoke(
        app, ["depend", b, "--on", a], env={"TASKMAN_TASKS_DIR": str(tree)}
    )
    assert result.exit_code == 0
    result = runner.invoke(app, ["discard", a], env={"TASKMAN_TASKS_DIR": str(tree)})
    assert result.exit_code != 0
    close(tree, a)
    with pytest.raises(ValueError, match="immutable"):
        edit_item(tree, a, "No", "No", "No")
    assert find_item_by_id(tree, a).exists()


def test_consolidation_preserves_bytes_references_and_hierarchy(tmp_path):
    tree = tmp_path / "tasks"
    epic = new(tree, "Delivered", "epic")
    child = new(tree, "Child", parent=epic)
    runner.invoke(app, ["finalize", epic], env={"TASKMAN_TASKS_DIR": str(tree)})
    close(tree, child)
    close(tree, epic)
    other = new(tree, "Follow up")
    runner.invoke(
        app, ["depend", other, "--on", child], env={"TASKMAN_TASKS_DIR": str(tree)}
    )
    before = {p: p.read_bytes() for p in tree.rglob("*.md")}
    result = consolidate(tree, [epic, child], "Delivery summary")
    assert set(result["ids"]) == {epic, child}
    assert {r["id"] for r in collection(tree)} == {other}
    assert {r["id"] for r in collection(tree, True)} == {epic, child, other}
    assert all(p.read_bytes() == data for p, data in before.items())
    assert "Fixture prepared" in result["summary"]
    assert f"_{epic}.md" in result["summary"]
    assert (
        runner.invoke(
            app, ["context", child], env={"TASKMAN_TASKS_DIR": str(tree)}
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["validate"], env={"TASKMAN_TASKS_DIR": str(tree)}).exit_code
        == 0
    )
    assert (
        child
        not in runner.invoke(
            app, ["list", "--json"], env={"TASKMAN_TASKS_DIR": str(tree)}
        ).output
    )
    with pytest.raises(ValueError, match="already"):
        consolidate(tree, [child], "Again")
    with pytest.raises(ValueError, match="done"):
        consolidate(tree, [other], "No")


def test_failed_publication_and_malformed_manifest_never_hide_work(
    tmp_path, monkeypatch
):
    import taskman.model.collection as module

    tree = tmp_path / "tasks"
    id = new(tree, "Closed")
    close(tree, id)
    original = module.atomic_text

    def fail(path, text):
        if path.suffix == ".json":
            raise OSError("simulated publication failure")
        original(path, text)

    monkeypatch.setattr(module, "atomic_text", fail)
    with pytest.raises(OSError):
        consolidate(tree, [id], "Failure")
    assert {r["id"] for r in collection(tree)} == {id}
    (module.history_dir(tree) / "bad.json").write_text("{bad")
    assert histories(tree) == []
    assert {r["id"] for r in collection(tree)} == {id}


def test_consolidation_under_open_parent_and_concise_excerpts(tmp_path):
    tree = tmp_path / "tasks"
    parent = new(tree, "Open parent", "epic")
    id = new(tree, "Closed child", parent=parent)
    runner.invoke(app, ["finalize", parent], env={"TASKMAN_TASKS_DIR": str(tree)})
    prepare(tree, id)
    runner.invoke(
        app,
        ["record", id, "--kind", "summary", "--actor", "test", "--body-file", "-"],
        input="word " * 1000,
        env={"TASKMAN_TASKS_DIR": str(tree)},
    )
    runner.invoke(app, ["close", id], env={"TASKMAN_TASKS_DIR": str(tree)})
    result = consolidate(tree, [id], "Child outcome")
    assert len(result["summary"]) < 1500
    assert [r["id"] for r in collection(tree)] == [parent]


def test_http_query_edit_discard_and_consolidation(tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from taskman.commands.ui import _make_handler

    tree = tmp_path / "tasks"
    id = new(tree, "Draft")
    done = new(tree, "Done")
    close(tree, done)
    server = ThreadingHTTPServer(("localhost", 0), _make_handler(tree))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(endpoint, data, headers=None):
        req = Request(
            f"http://localhost:{server.server_port}/api/" + endpoint,
            data=json.dumps(data).encode(),
            headers=headers or {"Content-Type": "application/json", "X-Taskman": "1"},
        )
        with urlopen(req) as response:
            return json.load(response)

    try:
        result = post("query", {"filters": [{"field": "status", "value": "done"}]})
        assert [r["id"] for r in result["rows"]] == [done]
        assert "draft" in result["fields"]
        post(
            "edit",
            {"id": id, "title": "Edited", "body": "Updated", "reason": "Clear scope"},
        )
        with pytest.raises(HTTPError) as error:
            post("edit", {"id": done, "title": "No", "body": "No", "reason": "No"})
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            post("query", {}, headers={"Content-Type": "application/json"})
        assert error.value.code == 403
        with pytest.raises(HTTPError):
            post("consolidate", {"ids": [id], "title": "Not done"})
        post("discard", {"id": id})
        assert find_item_by_id(tree, id) is None
        result = post("consolidate", {"ids": [done], "title": "Finished delivery"})
        assert post("query", {})["rows"] == []
        with urlopen(
            f"http://localhost:{server.server_port}/api/history/" + result["key"]
        ) as response:
            assert b"Finished delivery" in response.read()
    finally:
        server.shutdown()
        server.server_close()


def test_cli_missing_field_keeps_present_null_distinct(tmp_path):
    tree = tmp_path / "tasks"
    first = new(tree, "Has null")
    second = new(tree, "Missing")
    path = find_item_by_id(tree, first)
    meta, body = read_file(path)
    meta["extension"] = None
    write_file(path, meta, body)
    result = runner.invoke(
        app, ["browse", "--missing", "extension"], env={"TASKMAN_TASKS_DIR": str(tree)}
    )
    assert result.exit_code == 0, result.output
    assert [r["id"] for r in json.loads(result.output)] == [second]
