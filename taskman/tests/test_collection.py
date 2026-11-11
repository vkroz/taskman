import json
import pytest
from typer.testing import CliRunner
from taskman import lifecycle
from taskman.cli import app
from taskman.model.collection import collection, query
from taskman.model.ordering import ordered_siblings, reorder_item
from taskman.commands.collection import edit_item
from taskman.storage.sql_store import open_sql_store
from tests import sql_helpers as sql

runner = CliRunner()


@pytest.fixture
def store(sqlite_env):
    store = open_sql_store(sqlite_env, actor="pytest")
    yield store
    store.close()


def test_query_combines_filters_keyword_epic_sort_and_every_metadata_key(store):
    epic = sql.new("Epic", "epic")
    a = sql.new("Zebra", parent=epic, body="Needle")
    b = sql.new("Alpha", parent=epic)
    sql.run("label", a, "--add", "one", "--add", "two")
    rows = collection(store)
    meta = next(r["metadata"] for r in rows if r["id"] == a)
    result = query(
        rows,
        [
            {"field": "labels", "op": "contains", "value": "TW"},
            {"field": "labels", "op": "eq", "value": "two"},
        ],
        keyword="needle",
        epic=epic,
    )
    assert [r["id"] for r in result] == [epic, a]
    assert result[0]["context"] and not result[1]["context"]
    assert [r["id"] for r in query(rows, sort="title")] == [epic, b, a]
    assert meta["assignee"] is None
    assert any(
        r["id"] == a and not r["context"]
        for r in query(rows, [{"field": "assignee", "op": "exists"}])
    )
    for key, value in meta.items():
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        assert any(
            r["id"] == a and not r["context"]
            for r in query(rows, [{"field": key, "op": "contains", "value": text}])
        )
    output = sql.run(
        "browse", "--contains", "labels=tw", "--where", "labels=two", "--epic", epic, "--keyword", "needle"
    )
    assert [r["id"] for r in json.loads(output)] == [epic, a]


def test_edit_and_delete_preserve_closed_and_referenced_work(store):
    a = sql.new("Original")
    b = sql.new("Reference")
    edit_item(store, a, "Changed", "Body", "Clarify the requirement")
    shown = sql.show(a)
    assert shown["frontmatter"]["title"] == "Changed" and shown["body"] == "Body"
    assert shown["frontmatter"]["journal"][-1]["text"] == "Clarify the requirement"
    sql.run("depend", b, "--on", a)
    assert sql.runner.invoke(app, ["discard", a]).exit_code != 0
    sql.prepare(a)
    sql.run("close", a)
    with pytest.raises(ValueError, match="immutable"):
        edit_item(store, a, "No", "No", "No")
    assert store.get(a) is not None


def test_http_query_edit_and_discard(store):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from taskman.commands.ui import _make_handler

    id = sql.new("Draft")
    done = sql.new("Done")
    sql.prepare(done)
    sql.run("close", done)
    server = ThreadingHTTPServer(("localhost", 0), _make_handler(store))
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
        assert sql.show(id)["frontmatter"]["title"] == "Edited"
        with pytest.raises(HTTPError) as error:
            post("edit", {"id": done, "title": "No", "body": "No", "reason": "No"})
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            post("query", {}, headers={"Content-Type": "application/json"})
        assert error.value.code == 403
        post("discard", {"id": id})
        assert store.get(id) is None
        assert [r["id"] for r in post("query", {})["rows"]] == [done]
    finally:
        server.shutdown()
        server.server_close()


def test_cli_missing_and_exists_treat_null_assignee_as_present(store):
    first = sql.new("First")
    second = sql.new("Second")
    assert json.loads(sql.run("browse", "--missing", "assignee")) == []
    assert [r["id"] for r in json.loads(sql.run("browse", "--exists", "assignee"))] == [first, second]
    assert [r["id"] for r in json.loads(sql.run("browse", "--missing", "extension"))] == [first, second]


def test_reorder_moves_up_down_boundaries_and_persists_without_mutating_items(store):
    parent = sql.new("Parent", "epic")
    ids = [sql.new(title, parent=parent) for title in ("A", "B", "C", "D")]

    def records():
        # A rank change updates changed_at; the record itself stays as it was.
        return {
            item.id: {k: v for k, v in item.metadata.items() if k != "changed_at"} | {"body": item.body}
            for item in store.items()
        }

    before = records()

    assert reorder_item(store, ids[2], 1) == [ids[2], ids[0], ids[1], ids[3]]
    assert reorder_item(store, ids[2], 4) == [ids[0], ids[1], ids[3], ids[2]]
    assert reorder_item(store, ids[2], 1) == [ids[2], ids[0], ids[1], ids[3]]
    assert [row["id"] for row in ordered_siblings(store, ids[0])] == [
        ids[2], ids[0], ids[1], ids[3]
    ]
    assert records() == before
    rows = {row["id"]: row for row in collection(store)}
    assert rows[ids[2]]["parent_id"] == parent
    assert rows[ids[2]]["metadata"]["status"] == "backlog"


def test_reorder_cli_noop_and_invalid_requests_are_atomic(store):
    ids = [sql.new(title) for title in ("A", "B", "C")]

    def activity():
        return [store.activity(id) for id in ids]

    output = sql.run("reorder", ids[1], "--position", "1")
    assert json.loads(output)["order"] == [ids[1], ids[0], ids[2]]
    persisted, changes = store.sibling_order(), activity()

    sql.run("reorder", ids[1], "--position", "1")
    assert (store.sibling_order(), activity()) == (persisted, changes)
    for args in (["reorder", "TM-99", "--position", "1"], ["reorder", ids[0], "--position", "9"]):
        result = sql.runner.invoke(app, args)
        assert result.exit_code != 0
        assert (store.sibling_order(), activity()) == (persisted, changes)


def test_reorder_has_no_two_digit_capacity_limit_and_preserves_hidden_siblings(store):
    with store.lock():
        ids = [lifecycle.create(store, f"Task {index}", "task", None, project="TM").id for index in range(105)]
    order = reorder_item(store, ids[-1], 2)
    assert len(order) == 105
    assert order[:4] == [ids[0], ids[-1], ids[1], ids[2]]
    visible = query(collection(store), keyword="Task 0")
    assert [row["id"] for row in visible] == [ids[0]]
    assert [row["id"] for row in ordered_siblings(store, ids[0])][:4] == order[:4]


def test_reorder_refuses_completed_records(store):
    """Decision M2: the change to done is an item's last change, its rank included."""
    first = sql.new("Done first")
    second = sql.new("Done second")
    for key in (first, second):
        sql.prepare(key)
        sql.run("close", key)
    before = {item.id: item.metadata for item in store.items()}
    result = sql.runner.invoke(app, ["reorder", second, "--position", "1"])
    assert result.exit_code != 0
    assert "immutable" in result.output
    assert {item.id: item.metadata for item in store.items()} == before
    assert store.sibling_order()["root"] == [first, second]


def test_hide_done_filter_composes_with_other_filters_and_context(store):
    epic = sql.new("Epic", "epic")
    open_id = sql.new("Needle open", parent=epic)
    done_id = sql.new("Needle done", parent=epic)
    sql.run("finalize", epic)
    sql.prepare(done_id)
    sql.run("close", done_id)
    rows = collection(store)
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
