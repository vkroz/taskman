import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from taskman.cli import app
from taskman.model.collection import collection, query, consolidate, histories
from taskman.model.ordering import ordered_siblings, reorder_item
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


def test_reorder_moves_up_down_boundaries_and_persists_without_mutating_items(tmp_path):
    tree = tmp_path / "tasks"
    parent = new(tree, "Parent", "epic")
    ids = [new(tree, title, parent=parent) for title in ("A", "B", "C", "D")]
    before = {path: path.read_bytes() for path in tree.rglob("*.md")}

    assert reorder_item(tree, ids[2], 1) == [ids[2], ids[0], ids[1], ids[3]]
    assert reorder_item(tree, ids[2], 4) == [ids[0], ids[1], ids[3], ids[2]]
    assert reorder_item(tree, ids[2], 1) == [ids[2], ids[0], ids[1], ids[3]]
    assert [row["id"] for row in ordered_siblings(tree, ids[0])] == [
        ids[2], ids[0], ids[1], ids[3]
    ]
    assert {path: path.read_bytes() for path in tree.rglob("*.md")} == before
    rows = {row["id"]: row for row in collection(tree)}
    assert rows[ids[2]]["parent_id"] == parent
    assert rows[ids[2]]["metadata"]["status"] == "backlog"


def test_reorder_cli_noop_and_invalid_requests_are_atomic(tmp_path):
    tree = tmp_path / "tasks"
    ids = [new(tree, title) for title in ("A", "B", "C")]
    order_file = tree / ".taskman-order.json"
    result = runner.invoke(
        app, ["reorder", ids[1], "--position", "1"], env={"TASKMAN_TASKS_DIR": str(tree)}
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["order"] == [ids[1], ids[0], ids[2]]
    persisted = order_file.read_bytes()

    result = runner.invoke(
        app, ["reorder", ids[1], "--position", "1"], env={"TASKMAN_TASKS_DIR": str(tree)}
    )
    assert result.exit_code == 0
    assert order_file.read_bytes() == persisted
    for args in (["reorder", "999999999", "--position", "1"], ["reorder", ids[0], "--position", "9"]):
        result = runner.invoke(app, args, env={"TASKMAN_TASKS_DIR": str(tree)})
        assert result.exit_code != 0
        assert order_file.read_bytes() == persisted


def test_reorder_has_no_two_digit_capacity_limit_and_preserves_hidden_siblings(tmp_path):
    tree = tmp_path / "tasks"
    ids = [new(tree, f"Task {index}") for index in range(105)]
    order = reorder_item(tree, ids[-1], 2)
    assert len(order) == 105
    assert order[:4] == [ids[0], ids[-1], ids[1], ids[2]]
    visible = query(collection(tree), keyword="Task 0")
    assert [row["id"] for row in visible] == [ids[0]]
    assert [row["id"] for row in ordered_siblings(tree, ids[0])][:4] == order[:4]


def test_reorder_does_not_rewrite_completed_records(tmp_path):
    tree = tmp_path / "tasks"
    first = new(tree, "Done first")
    second = new(tree, "Done second")
    close(tree, first)
    close(tree, second)
    before = {path: path.read_bytes() for path in tree.rglob("*.md")}
    assert reorder_item(tree, second, 1) == [second, first]
    assert {path: path.read_bytes() for path in tree.rglob("*.md")} == before


def test_hide_done_filter_composes_with_other_filters_and_context(tmp_path):
    tree = tmp_path / "tasks"
    epic = new(tree, "Epic", "epic")
    open_id = new(tree, "Needle open", parent=epic)
    done_id = new(tree, "Needle done", parent=epic)
    runner.invoke(app, ["finalize", epic], env={"TASKMAN_TASKS_DIR": str(tree)})
    close(tree, done_id)
    rows = collection(tree)
    hidden = query(
        rows,
        [{"field": "status", "op": "neq", "value": "done"}],
        keyword="needle",
        epic=epic,
    )
    assert [row["id"] for row in hidden] == [epic, open_id]
    conflict = query(
        rows,
        [
            {"field": "status", "op": "eq", "value": "done"},
            {"field": "status", "op": "neq", "value": "done"},
        ],
        epic=epic,
    )
    assert conflict == []
    restored = query(
        rows,
        [{"field": "status", "op": "eq", "value": "done"}],
        keyword="needle",
        epic=epic,
    )
    assert [row["id"] for row in restored] == [epic, done_id]
