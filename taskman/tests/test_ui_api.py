"""Workspace API on SQLite and Postgres: views, derived signals, detail, conflicts, fields and order."""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from taskman import lifecycle
from taskman.commands import ui
from taskman.commands.ui import _make_handler
from taskman.storage.schema import upgrade
from taskman.storage.sql import connect
from taskman.storage.sql_store import open_sql_store
from tests.ui_seed import seed

HEADERS = {"Content-Type": "application/json", "X-Taskman": "1"}


class Api:
    def __init__(self, store):
        self.store = store
        self.server = ThreadingHTTPServer(("localhost", 0), _make_handler(store))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://localhost:{self.server.server_port}"

    def raw(self, path, data=None):
        """Status, content type and body text of a request; POST when ``data`` is given."""
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(self.base + path, data=body, headers=HEADERS)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers["Content-Type"], response.read().decode()
        except HTTPError as error:
            return error.code, error.headers["Content-Type"], error.read().decode()

    def __call__(self, path, data=None, status=200):
        code, content_type, text = self.raw(path, data)
        assert content_type == "application/json"
        value = json.loads(text)
        assert code == status, value
        return value

    def query(self, **data):
        return self("/api/query", data)

    def ids(self, **data):
        """Keys of the rows that match, without context ancestors."""
        return [row["id"] for row in self.query(**data)["rows"] if not row["context"]]


@pytest.fixture
def store(sql_settings):
    conn, dialect = connect(sql_settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    store = open_sql_store(sql_settings, actor="Vlad")
    yield store
    store.close()


@pytest.fixture
def api(store):
    api = Api(store)
    yield api
    api.server.shutdown()
    api.server.server_close()


@pytest.fixture
def seeded(store, api):
    seed(store)
    return api


def _create(api, title, **fields):
    return api("/api/items", {"title": title, "type": "task", "body": "Body", **fields}, status=201)["id"]


# ---- /api/query --------------------------------------------------------------


def test_query_returns_projects_view_counts_and_view_rows(seeded):
    result = seeded.query(view="attention")
    assert result["actor"] == "Vlad"
    assert result["projects"] == [{"key": "TM", "name": "TM", "open": 9}, {"key": "RES", "name": "RES", "open": 12},
                                  {"key": "DIV", "name": "DIV", "open": 1}]
    assert result["counts"] == {"attention": 1, "active": 7, "ready": 5, "blocked": 4, "drafts": 6, "backlog": 15,
                                "done": 6}
    assert seeded.ids(view="attention") == ["RES-4"]
    assert set(seeded.ids(view="ready")) == {"RES-5", "RES-7", "RES-9", "RES-11", "RES-13"}
    assert set(seeded.ids(view="blocked")) == {"TM-8", "TM-9", "TM-10", "TM-11"}
    assert set(seeded.ids(view="drafts")) == {"TM-12", "TM-13", "RES-6", "RES-8", "RES-10", "RES-12"}
    assert len(seeded.ids(view="active")) == 7
    assert len(seeded.ids(view="backlog")) == 15
    assert set(seeded.ids(view="done")) == {"TM-2", "TM-3", "TM-4", "TM-5", "RES-3", "DIV-2"}
    # Context ancestors come with the matches, flagged.
    rows = seeded.query(view="attention")["rows"]
    assert [(row["id"], row["context"], row["depth"]) for row in rows] == [
        ("RES-1", True, 0), ("RES-2", True, 1), ("RES-4", False, 2)]


def test_query_rows_are_slim_and_carry_derived_signals(seeded):
    rows = {row["id"]: row for row in seeded.query()["rows"]}
    assert len(rows) == 28
    assert set(rows["RES-4"]) == {
        "id", "project", "parent_id", "order", "depth", "context", "type", "title", "status", "draft", "assignee",
        "labels", "depends_on", "created_at", "changed_at", "state", "blockers", "progress", "verification",
        "attention", "updated_at"}
    res4 = rows["RES-4"]
    assert (res4["assignee"], res4["labels"], res4["verification"], res4["attention"], res4["state"]) == (
        "Vlad", ["aws", "bug"], "fail", "verification_failed", "active")
    assert (rows["TM-10"]["state"], rows["TM-10"]["blockers"]) == ("blocked", ["TM-8", "TM-9"])
    assert (rows["TM-8"]["blockers"], rows["TM-8"]["assignee"]) == (["TM-7"], "Codex")
    assert rows["TM-12"]["blockers"] == ["TM-6"]  # an ancestor's dependency also waits
    assert {key: rows[key]["progress"] for key in ("TM-1", "TM-6", "RES-1")} == {
        "TM-1": {"done": 3, "total": 8}, "TM-6": {"done": 0, "total": 4}, "RES-1": {"done": 1, "total": 2}}
    assert rows["TM-7"]["progress"] is None
    assert {rows[key]["state"] for key in ("TM-12", "TM-13")} == {"draft"}
    assert (rows["RES-5"]["state"], rows["TM-7"]["state"], rows["TM-2"]["state"]) == ("ready", "active", "done")
    assert rows["TM-2"]["verification"] == "pass"
    assert rows["TM-1"]["verification"] is None
    assert rows["TM-7"]["updated_at"] >= rows["TM-7"]["changed_at"]
    # Rows are grouped by project in project order.
    projects = [row["project"] for row in seeded.query()["rows"]]
    assert projects == sorted(projects, key=["TM", "RES", "DIV"].index)


def test_query_project_scope_facets_sort_and_filters(seeded):
    scoped = seeded.query(project="RES", view="backlog")
    assert {row["project"] for row in scoped["rows"]} == {"RES"}
    assert scoped["counts"]["backlog"] == 9 and scoped["counts"]["attention"] == 1
    assert scoped["facets"]["labels"] == {"bug": 1, "ui": 1}
    assert scoped["facets"]["state"] == {"draft": 4, "ready": 5}
    facets = seeded.query(view="active")["facets"]
    assert facets["assignee"] == {"Claude Code": 1, "Vlad": 1}
    assert facets["type"] == {"epic": 3, "feat": 2, "task": 2}
    assert seeded.ids(view="blocked", filters=[{"field": "state", "op": "eq", "value": "blocked"}],
                      keyword="remote") == ["TM-8", "TM-10"]
    assert seeded.ids(filters=[{"field": "labels", "op": "eq", "value": "backend"}]) == ["TM-7"]
    keys = seeded.ids(project="RES", sort="key")
    assert keys[keys.index("RES-5"):] == ["RES-5", "RES-6", "RES-7", "RES-8", "RES-9", "RES-10", "RES-11",
                                          "RES-12", "RES-13"]
    # Sort applies among siblings; the client flattens and sorts the Done view itself.
    assert seeded.ids(project="TM", view="done", sort="updated", descending=True) == ["TM-2", "TM-5", "TM-4", "TM-3"]


def test_responses_are_compact_json(seeded):
    _, _, text = seeded.raw("/api/query", {"view": "backlog"})
    assert '", "' not in text and '": ' not in text
    assert json.loads(text)["counts"]["backlog"] == 15


def test_show_etag_reports_unchanged_until_the_item_changes(seeded):
    first = seeded("/api/show/RES-5")
    assert seeded(f"/api/show/RES-5?etag={first['etag']}") == {"etag": first["etag"], "unchanged": True}
    assert "frontmatter" in seeded(f"/api/show/RES-7?etag={first['etag']}")
    seeded("/api/comment", {"id": "RES-5", "kind": "progress", "text": "Moved on"})
    changed = seeded(f"/api/show/RES-5?etag={first['etag']}")
    assert changed["etag"] != first["etag"] and "frontmatter" in changed
    assert seeded("/api/show/RES-99?etag=x", status=404) == {"error": "item not found: RES-99"}


def test_query_etag_reports_unchanged_until_data_changes(seeded):
    first = seeded.query(view="ready")
    assert seeded.query(view="ready", etag=first["etag"]) == {"etag": first["etag"], "unchanged": True}
    assert "rows" in seeded.query(view="active", etag=first["etag"])
    seeded("/api/assign", {"id": "RES-5", "assignee": "Codex"})
    changed = seeded.query(view="ready", etag=first["etag"])
    assert changed["etag"] != first["etag"] and "rows" in changed


def test_query_rejects_unknown_view_sort_and_filters(seeded):
    assert "Unknown view" in seeded("/api/query", {"view": "current"}, status=400)["error"]
    assert "Unknown sort" in seeded("/api/query", {"sort": "journal"}, status=400)["error"]
    assert "text" in seeded("/api/query", {"project": 3}, status=400)["error"]
    assert "Filters" in seeded("/api/query", {"filters": [{"op": "eq"}]}, status=400)["error"]


def test_attention_includes_handoffs_and_items_ready_to_close(api, store):
    for title in ("Handoff", "Closable", "Plain"):
        _create(api, title, project="TM")
    lifecycle.finalize(store, "TM-2")
    lifecycle.record(store, "TM-1", "handoff", "Codex", "Next agent: continue.")
    lifecycle.record(store, "TM-2", "summary", "Codex", "Done.")
    lifecycle.record(store, "TM-2", "verification", "Codex", "Passed.", "pass")
    rows = {row["id"]: row["attention"] for row in api.query()["rows"]}
    assert rows == {"TM-1": "handoff", "TM-2": "ready_to_close", "TM-3": None}
    lifecycle.record(store, "TM-2", "decision", "Codex", "Scope changed.")  # the pass is now stale
    row = api.query()["rows"][1]
    assert (row["id"], row["verification"], row["attention"]) == ("TM-2", "stale", None)


# ---- /api/show and GET routing -----------------------------------------------


def test_show_adds_relations_close_gates_and_discard_state(seeded):
    tm10 = seeded("/api/show/TM-10")
    assert [a["id"] for a in tm10["ancestors"]] == ["TM-1", "TM-6"]
    assert tm10["ancestors"][1] == {"id": "TM-6", "type": "feat", "title": "Serve Taskman over HTTP with a remote backend"}
    assert [(d["id"], d["state"]) for d in tm10["depends_on"]] == [("TM-8", "blocked"), ("TM-9", "blocked")]
    assert (tm10["row"]["id"], tm10["row"]["depth"], tm10["row"]["blockers"]) == ("TM-10", 2, ["TM-8", "TM-9"])
    assert [d["id"] for d in seeded("/api/show/TM-7")["dependents"]] == ["TM-8", "TM-9"]
    tm11 = seeded("/api/show/TM-11")
    assert tm11["children"] == [{"id": "TM-12", "type": "task", "title": "Write the deploy document",
                                 "state": "draft", "assignee": None}]
    assert [d["id"] for d in tm11["depends_on"]] == ["TM-6"]
    gates = {gate["check"]: gate for gate in seeded("/api/show/RES-4")["close_gates"]}
    assert [g["ok"] for g in gates.values()] == [True, True, True, False, False]
    assert (gates["verification"]["status"], gates["summary"]["status"]) == ("fail", "missing")
    assert gates["verification"]["detail"] == "Verification for rev 0: fail"
    tm1 = {gate["check"]: gate for gate in seeded("/api/show/TM-1")["close_gates"]}
    assert (tm1["children"]["ok"], tm1["children"]["detail"]) == (False, "Sub-items done (4/11)")
    assert seeded("/api/show/TM-2")["close_gates"] is None
    assert (seeded("/api/show/TM-7")["can_discard"], seeded("/api/show/TM-7")["discard_blocked_reason"]) == (
        False, "not a draft")
    assert (seeded("/api/show/TM-13")["can_discard"], seeded("/api/show/TM-13")["discard_blocked_reason"]) == (
        True, None)
    # The existing record stays for the timeline.
    assert seeded("/api/show/RES-4")["frontmatter"]["journal"][-1]["result"] == "fail"


def test_show_names_why_a_draft_cannot_be_discarded(api):
    parent = _create(api, "Parent", type="feat", project="TM")
    _create(api, "Child", parent=parent)
    other = _create(api, "Other", project="TM")
    assert api(f"/api/show/{parent}")["discard_blocked_reason"] == "has 1 child"
    lifecycle.set_dependencies(api.store, other, ["TM-2"])
    assert api("/api/show/TM-2")["discard_blocked_reason"] == "TM-3 depends on it"


def test_get_errors_are_json_and_item_paths_serve_the_page(seeded, monkeypatch):
    assert seeded("/api/show/RES-99", status=404) == {"error": "item not found: RES-99"}
    assert "Not found" in seeded("/api/nothing", status=404)["error"]
    assert "Not found" in seeded("/tm-7", status=404)["error"]
    for path in ("/", "/TM-7", "/RES-99", "/?item=TM-7&view=done"):
        status, content_type, text = seeded.raw(path)
        assert (status, content_type) == (200, "text/html; charset=utf-8")
        assert 'id="preview"' in text
    request = urllib.request.Request(seeded.base + "/api/show/TM-7", headers={"Host": "example.com"})
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(request)
    assert (error.value.code, json.load(error.value)["error"]) == (403, "Only local Taskman UI requests are accepted.")
    monkeypatch.setattr(ui, "show", lambda store, item_id, etag: 1 / 0)
    assert "division by zero" in seeded("/api/show/TM-7", status=500)["error"]


# ---- /api/edit -----------------------------------------------------------------


def test_edit_with_a_stale_base_returns_409_and_changes_nothing(api, store, sql_settings):
    key = _create(api, "Item", project="TM")
    agent = open_sql_store(sql_settings, actor="Codex")
    try:
        lifecycle.edit(agent, key, "Their title", "Their body", "Agent refined it")
    finally:
        agent.close()
    before = store.get(key)
    conflict = api("/api/edit", {"id": key, "title": "Mine", "body": "My body", "reason": "Mine",
                                 "base_title": "Item", "base_body": "Body"}, status=409)
    assert "changed by Codex" in conflict["error"]
    assert {k: conflict["current"][k] for k in ("title", "body", "actor")} == {
        "title": "Their title", "body": "Their body", "actor": "Codex"}
    assert conflict["current"]["changed_at"]
    assert store.get(key) == before
    saved = api("/api/edit", {"id": key, "title": "Mine", "body": "My body", "reason": "Mine",
                              "base_title": "Their title", "base_body": "Their body"})
    assert (saved["ok"], saved["row"]["title"]) == (True, "Mine")


def test_edit_after_a_child_change_is_not_a_conflict(api, store):
    parent = _create(api, "Parent", type="feat", project="TM")
    child = _create(api, "Child", parent=parent)
    opened = api(f"/api/show/{parent}")
    lifecycle.record(store, child, "decision", "Codex", "Changed the child.")  # bumps the parent revision
    assert store.get(parent).metadata["revision"] > opened["frontmatter"]["revision"]
    api("/api/edit", {"id": parent, "title": "Parent v2", "body": "Body", "reason": "Rename",
                      "base_title": opened["frontmatter"]["title"], "base_body": opened["body"]})
    assert store.get(parent).title == "Parent v2"


def test_edit_journals_the_store_actor_and_allows_title_only_drafts(api, store):
    key = _create(api, "Draft", project="TM")
    api("/api/edit", {"id": key, "title": "Draft", "body": "", "reason": "Title only"})
    item = store.get(key)
    assert (item.body, item.metadata["journal"][-1]["actor"]) == ("", "Vlad")
    lifecycle.finalize(store, key)
    error = api("/api/edit", {"id": key, "title": "Draft", "body": " ", "reason": "Empty"}, status=400)["error"]
    assert "must not be empty" in error
    assert "Explain" in api("/api/edit", {"id": key, "title": "Draft", "body": "B", "reason": ""}, status=400)["error"]
    assert "not found" in api("/api/edit", {"id": "TM-99", "title": "T", "body": "B", "reason": "R",
                                            "base_title": "T"}, status=400)["error"]


def test_lifecycle_edit_records_the_store_actor(store):
    with store.lock():
        key = lifecycle.create(store, "Item", "task", None, "Body", project="TM").id
    edited = lifecycle.edit(store, key, "Renamed", "Body", "Clarify")
    assert edited.metadata["journal"][-1]["actor"] == store.actor == "Vlad"


# ---- /api/fields ---------------------------------------------------------------


def test_fields_set_assignee_and_labels_together(api, store):
    key = _create(api, "Item", project="TM")
    result = api("/api/fields", {"id": key, "assignee": "Codex", "labels": ["db", "ui"]})
    assert (result["row"]["assignee"], result["row"]["labels"]) == ("Codex", ["db", "ui"])
    api("/api/fields", {"id": key, "assignee": "Codex", "labels": ["db"]})
    api("/api/fields", {"id": key, "assignee": "", "labels": ["db"]})
    item = store.get(key)
    assert (item.metadata["assignee"], item.metadata["labels"]) == (None, ["db"])
    assert [a["field"] for a in store.activity(key)] == ["created", "labels", "assignee", "labels", "assignee"]


def test_fields_are_atomic_and_validated(api, store):
    key = _create(api, "Item", project="TM")
    before = store.get(key)
    assert "label" in api("/api/fields", {"id": key, "assignee": "bob", "labels": ["Bad Label"]}, status=400)["error"]
    assert "1-100" in api("/api/fields", {"id": key, "assignee": "x" * 101, "labels": ["ok"]}, status=400)["error"]
    assert "list" in api("/api/fields", {"id": key, "assignee": "bob", "labels": "db"}, status=400)["error"]
    assert "not found" in api("/api/fields", {"id": "TM-9", "assignee": "bob", "labels": []}, status=400)["error"]
    assert store.get(key) == before
    lifecycle.finalize(store, key)
    lifecycle.record(store, key, "summary", "Codex", "Done.")
    lifecycle.record(store, key, "verification", "Codex", "Passed.", "pass")
    lifecycle.close(store, key)
    assert "immutable" in api("/api/fields", {"id": key, "assignee": "bob", "labels": []}, status=400)["error"]


# ---- per-project order ------------------------------------------------------------


def test_top_level_order_is_kept_per_project(api, store):
    for title, project in (("T1", "TM"), ("R1", "RES"), ("T2", "TM"), ("R2", "RES"), ("T3", "TM")):
        _create(api, title, project=project)
    assert store.sibling_order() == {"root:TM": ["TM-1", "TM-2", "TM-3"], "root:RES": ["RES-1", "RES-2"]}
    assert api("/api/reorder", {"id": "RES-2", "position": 1})["order"] == ["RES-2", "RES-1"]
    # A move outside the project's top level is not a position it can take.
    assert "between 1 and 2" in api("/api/reorder", {"id": "RES-2", "position": 3}, status=400)["error"]
    assert api("/api/reorder", {"id": "TM-3", "position": 1})["order"] == ["TM-3", "TM-1", "TM-2"]
    rows = api.query()["rows"]
    assert [(row["id"], row["order"]) for row in rows] == [
        ("TM-3", 0), ("TM-1", 1), ("TM-2", 2), ("RES-2", 0), ("RES-1", 1)]
    assert store.sibling_order() == {"root:TM": ["TM-3", "TM-1", "TM-2"], "root:RES": ["RES-2", "RES-1"]}


# ---- lifecycle gates -------------------------------------------------------------


def _gate_fixtures(store):
    """Ten open items in distinct close states. Returns their keys."""
    def item(title, parent=None, final=True):
        key = lifecycle.create(store, title, "task" if parent else "feat", parent, "Body",
                               project=None if parent else "TM").id
        if final:
            lifecycle.finalize(store, key)
        return key

    def evidence(key, *kinds, result="pass"):
        for kind in kinds:
            lifecycle.record(store, key, kind, "Codex", f"{kind}.", result if kind == "verification" else None)

    keys = []
    keys.append(item("Draft", final=False))
    evidence(keys[-1], "summary", "verification")
    keys.append(item("No evidence"))
    keys.append(item("Summary only"))
    evidence(keys[-1], "summary")
    keys.append(item("Verification only"))
    evidence(keys[-1], "verification")
    keys.append(item("Complete"))
    evidence(keys[-1], "summary", "verification")
    keys.append(item("Failed"))
    evidence(keys[-1], "summary", "verification", result="fail")
    keys.append(item("Stale"))
    evidence(keys[-1], "summary", "verification", "decision")
    keys.append(item("Blocked result"))
    evidence(keys[-1], "summary", "verification", result="blocked")
    keys.append(item("Waiting"))
    lifecycle.set_dependencies(store, keys[-1], [keys[1]])
    evidence(keys[-1], "summary", "verification")
    parent = item("Open child")
    child = item("Child of open", parent)
    evidence(parent, "summary", "verification")
    keys.append(parent)
    draft_parent = item("Draft parent", final=False)
    keys.append(item("Child of draft", draft_parent))
    evidence(keys[-1], "summary", "verification")
    assert child
    return keys


def test_close_gates_agree_with_close(store):
    keys = _gate_fixtures(store)
    outcomes = {}
    for key in keys:
        items = {item.id: item for item in store.items()}
        gates = lifecycle.close_gates(items[key], items)
        try:
            lifecycle.close(store, key)
            closed = True
        except lifecycle.LifecycleError:
            closed = False
        assert closed == all(gate["ok"] for gate in gates), (key, gates)
        outcomes[store.get(key).title] = [gate["check"] for gate in gates if not gate["ok"]]
    assert outcomes == {
        "Draft": ["finalized"], "No evidence": ["summary", "verification"], "Summary only": ["verification"],
        "Verification only": ["summary"], "Complete": [], "Failed": ["verification"],
        "Stale": ["summary", "verification"], "Blocked result": ["verification"], "Waiting": ["dependencies"],
        "Open child": ["children"], "Child of draft": ["finalized"]}


def test_close_gate_details_name_the_failing_items(store):
    keys = _gate_fixtures(store)
    items = {item.id: item for item in store.items()}
    by_title = {item.title: item for item in items.values()}

    def gates(title):
        return {g["check"]: g for g in lifecycle.close_gates(by_title[title], items)}

    assert gates("Waiting")["dependencies"] == {"check": "dependencies", "ok": False,
                                                "detail": f"Waiting on {keys[1]}", "refs": [keys[1]]}
    assert gates("Child of draft")["finalized"]["detail"] == f"Draft ancestor: {by_title['Draft parent'].id}"
    assert gates("Draft")["finalized"]["detail"] == "Not finalized (draft)"
    stale = gates("Stale")["verification"]
    assert (stale["status"], stale["detail"]) == ("stale", "Verification for rev 1: stale at rev 0")
    assert gates("Open child")["children"]["refs"] == [by_title["Child of open"].id]
    assert gates("Complete")["dependencies"]["detail"] == "No dependencies"


def test_discard_blocker_agrees_with_discard(store):
    with store.lock():
        feat = lifecycle.create(store, "Feature", "feat", None, project="TM").id
        child = lifecycle.create(store, "Child", "task", feat).id
        other = lifecycle.create(store, "Other", "task", None, project="TM").id
        final = lifecycle.create(store, "Final", "task", None, project="TM").id
    lifecycle.set_dependencies(store, other, [child])
    lifecycle.finalize(store, final)
    for key in (feat, child, final, other):
        items = {item.id: item for item in store.items()}
        reason = lifecycle.discard_blocker(items[key], items)
        try:
            lifecycle.discard(store, key)
            discarded = True
        except lifecycle.LifecycleError:
            discarded = False
        assert discarded == (reason is None), (key, reason)
    assert store.get(other) is None and store.get(feat) is not None
