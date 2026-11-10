"""Contract every SQL backend must satisfy: the work-item model of ``docs/data-model.md``.

Each backend supplies one function that returns settings for an empty
database. Every case uses ``taskman.lifecycle`` and the store, so a new
backend adds an entry to ``BACKENDS``, not new cases. The file backend has its
own cases in ``test_file_store.py`` until feature 261109005 removes it.
"""
from __future__ import annotations

import multiprocessing
import time

import pytest

from taskman import lifecycle
from taskman.lifecycle import LifecycleError, NoChange
from taskman.model.collection import consolidate
from taskman.model.ordering import reorder_item
from taskman.storage.schema import upgrade
from taskman.storage.settings import StorageSettings
from taskman.storage.sql import connect
from taskman.storage.sql_store import SqlStore, open_sql_store
from taskman.storage.store import StorageConfigError, StorageError
from tests.conftest import postgres_test_settings, reset_postgres


def _empty_postgres(tmp_path) -> StorageSettings:
    """Skipped without TASKMAN_TEST_DATABASE_URL; see conftest.py."""
    settings = postgres_test_settings()
    reset_postgres(settings)
    return settings


BACKENDS = {
    "sqlite": lambda tmp_path: StorageSettings("sqlite", sqlite_path=tmp_path / "taskman.db"),
    "postgres": _empty_postgres,
}


def _upgraded(settings: StorageSettings) -> StorageSettings:
    conn, dialect = connect(settings)
    try:
        upgrade(conn, dialect)
    finally:
        conn.close()
    return settings


@pytest.fixture(params=sorted(BACKENDS))
def empty_settings(request, tmp_path) -> StorageSettings:
    """Settings for an empty database without a schema."""
    return BACKENDS[request.param](tmp_path)


@pytest.fixture
def settings(empty_settings) -> StorageSettings:
    return _upgraded(empty_settings)


@pytest.fixture
def store(settings) -> SqlStore:
    store = open_sql_store(settings, actor="pytest")
    yield store
    store.conn.close()


def _new(store, title, item_type="task", parent=None, project="TM") -> str:
    return lifecycle.create(store, title, item_type, parent, project=None if parent else project).id


def _ready_to_close(store, item_id) -> None:
    for kind, result in (("summary", None), ("verification", "pass")):
        lifecycle.record(store, item_id, kind, "pytest", f"{kind} text", result)


def _closed(store, item_id) -> None:
    lifecycle.finalize(store, item_id)
    _ready_to_close(store, item_id)
    lifecycle.close(store, item_id)


def _fields(store, item_id) -> list[str]:
    return [entry["field"] for entry in store.activity(item_id)]


# ---- items, keys and projects -------------------------------------------


def test_create_starts_as_a_backlog_draft_with_a_key(store, settings):
    item = lifecycle.create(store, "Write docs", "task", None, project="TM")
    assert (item.id, item.status, item.draft, item.type, item.title) == ("TM-1", "backlog", True, "task", "Write docs")
    assert item.body == "# Write docs\n"
    assert item.location == f"{settings.backend}:TM-1"
    assert item.parent_id is None
    assert (item.metadata["project"], item.metadata["revision"], item.metadata["created_by"]) == ("TM", 0, "pytest")


def test_child_belongs_to_the_parent_project(store):
    epic = _new(store, "Epic", "epic")
    child = lifecycle.create(store, "Child", "task", epic)
    assert (child.id, child.parent_id, child.metadata["project"]) == ("TM-2", epic, "TM")
    with pytest.raises(LifecycleError, match="project of its parent"):
        lifecycle.create(store, "Elsewhere", "task", epic, project="AB")


def test_top_level_item_needs_a_valid_project_key(store):
    with pytest.raises(StorageError, match="needs a project key"):
        lifecycle.create(store, "Orphan", "task", None)
    for key in ("tm", "T", "1TM", "TOOLONGPROJ"):
        with pytest.raises(LifecycleError, match="project key"):
            lifecycle.create(store, "Bad", "task", None, project=key)


def test_get_unknown_or_malformed_key_returns_none(store):
    _new(store, "Only")
    for key in ("TM-999", "AB-1", "tm-1", "TM-01", "261109009", ""):
        assert store.get(key) is None


def test_projects_number_independently_in_one_database(store):
    keys = [_new(store, "A", project="TM"), _new(store, "B", project="AB"), _new(store, "C", project="TM")]
    assert keys == ["TM-1", "AB-1", "TM-2"]
    by_project = {}
    for item in store.items():
        by_project.setdefault(item.metadata["project"], set()).add(item.id)
    assert by_project == {"TM": {"TM-1", "TM-2"}, "AB": {"AB-1"}}


def test_dependencies_stay_inside_one_project(store):
    mine = _new(store, "Mine", project="TM")
    theirs = _new(store, "Theirs", project="AB")
    with pytest.raises(LifecycleError, match="inside one project"):
        lifecycle.set_dependencies(store, mine, [theirs])
    assert store.get(mine).depends_on == ()


def test_items_include_every_item_with_parent_links(store):
    epic = _new(store, "Epic", "epic")
    feat = _new(store, "Feature", "feat", epic)
    task = _new(store, "Task", "task", feat)
    other = _new(store, "Other")
    assert {item.id: item.parent_id for item in store.items()} == {epic: None, feat: epic, task: feat, other: None}


def test_metadata_keeps_the_journal_shape(store):
    dep = _new(store, "Dep")
    task = _new(store, "Task")
    lifecycle.set_dependencies(store, task, [dep])
    lifecycle.update_body(store, task, "Contract body text.\n")
    lifecycle.record(store, task, "decision", "Codex", "Chose X.")
    lifecycle.record(store, task, "verification", "pytest", "ran checks", "fail")
    item = store.get(task)
    assert item.body == "Contract body text.\n"
    assert item.depends_on == (dep,)
    journal = item.metadata["journal"]
    assert [(e["kind"], e["actor"], e["text"], e["revision"]) for e in journal] == [
        ("decision", "Codex", "Chose X.", 3),
        ("verification", "pytest", "ran checks", 3),
    ]
    assert "result" not in journal[0] and journal[1]["result"] == "fail"
    text = store.document(task)
    assert "title: Task" in text and "Contract body text." in text


# ---- structure ------------------------------------------------------------


def test_task_cannot_have_children(store):
    task = _new(store, "Leaf")
    with pytest.raises(LifecycleError, match="task = leaf invariant"):
        lifecycle.create(store, "Child", "task", task)


def test_closed_parent_rejects_new_children(store):
    feat = _new(store, "Feature", "feat")
    _closed(store, feat)
    with pytest.raises(LifecycleError, match="closed parents are immutable"):
        lifecycle.create(store, "Child", "task", feat)


def test_convert_to_task_requires_no_children(store):
    feat = _new(store, "Feature", "feat")
    _new(store, "Child", "task", feat)
    with pytest.raises(LifecycleError, match="has children"):
        lifecycle.convert(store, feat, "task")
    leaf = _new(store, "Leaf")
    assert lifecycle.convert(store, leaf, "feat").type == "feat"


# ---- dependencies and readiness -------------------------------------------


def test_dependencies_reject_unknown_keys_and_cycles(store):
    a = _new(store, "A")
    b = _new(store, "B")
    lifecycle.set_dependencies(store, a, [b])
    with pytest.raises(LifecycleError, match="cycle"):
        lifecycle.set_dependencies(store, b, [a])
    with pytest.raises(LifecycleError, match="not found"):
        lifecycle.set_dependencies(store, a, ["TM-999"])


def test_child_cannot_depend_on_its_parent(store):
    feat = _new(store, "Feature", "feat")
    child = _new(store, "Child", "task", feat)
    with pytest.raises(LifecycleError, match="cycle"):
        lifecycle.set_dependencies(store, child, [feat])


def test_activation_requires_finalized_items_and_done_dependencies(store):
    dep = _new(store, "Dep")
    work = _new(store, "Work")
    lifecycle.set_dependencies(store, work, [dep])
    with pytest.raises(LifecycleError, match="finalize"):
        lifecycle.move(store, work, "active")
    lifecycle.finalize(store, work)
    with pytest.raises(LifecycleError, match="dependency is not done"):
        lifecycle.move(store, work, "active")
    _closed(store, dep)
    assert lifecycle.move(store, work, "active").status == "active"
    with pytest.raises(NoChange):
        lifecycle.move(store, work, "active")


def test_ready_needs_finalized_unblocked_ancestors(store):
    def ready_ids():
        items = {item.id: item for item in store.items()}
        return {item_id for item_id, item in items.items() if lifecycle.is_ready(item, items)}

    feat = _new(store, "Feature", "feat")
    child = _new(store, "Child", "task", feat)
    dep = _new(store, "Dep")
    lifecycle.set_dependencies(store, feat, [dep])
    lifecycle.finalize(store, child)
    assert child not in ready_ids()  # parent is still a draft
    lifecycle.finalize(store, feat)
    assert child not in ready_ids()  # parent waits on dep
    lifecycle.finalize(store, dep)
    assert ready_ids() == {dep}
    _ready_to_close(store, dep)
    lifecycle.close(store, dep)
    assert ready_ids() == {feat, child}


# ---- comments, revisions and closure --------------------------------------


def test_verification_comments_require_a_result(store):
    item = _new(store, "Item")
    with pytest.raises(LifecycleError, match="verification requires"):
        lifecycle.record(store, item, "verification", "pytest", "ran checks")
    with pytest.raises(LifecycleError, match="unknown entry kind"):
        lifecycle.record(store, item, "note", "pytest", "text")
    assert store.get(item).metadata["journal"] == []


def test_close_requires_summary_and_passing_verification(store):
    item = _new(store, "Item")
    lifecycle.finalize(store, item)
    with pytest.raises(LifecycleError, match="passing verification"):
        lifecycle.close(store, item)
    lifecycle.record(store, item, "verification", "pytest", "failed", "fail")
    with pytest.raises(LifecycleError, match="passing verification"):
        lifecycle.close(store, item)
    lifecycle.record(store, item, "verification", "pytest", "passed", "pass")
    with pytest.raises(LifecycleError, match="summary"):
        lifecycle.close(store, item)
    lifecycle.record(store, item, "summary", "pytest", "done")
    assert lifecycle.close(store, item).status == "done"
    with pytest.raises(NoChange):
        lifecycle.close(store, item)


def test_close_requires_finished_descendants(store):
    feat = _new(store, "Feature", "feat")
    _new(store, "Child", "task", feat)
    lifecycle.finalize(store, feat)
    _ready_to_close(store, feat)
    with pytest.raises(LifecycleError, match="descendants not in done"):
        lifecycle.close(store, feat)


def test_body_change_invalidates_earlier_evidence(store):
    item = _new(store, "Item")
    lifecycle.finalize(store, item)
    _ready_to_close(store, item)
    lifecycle.update_body(store, item, "Changed requirements.\n")
    with pytest.raises(LifecycleError, match="current revision"):
        lifecycle.close(store, item)


def test_child_change_invalidates_parent_evidence(store):
    feat = _new(store, "Feature", "feat")
    child = _new(store, "Child", "task", feat)
    lifecycle.finalize(store, feat)
    _closed(store, child)
    _ready_to_close(store, feat)
    before = store.get(feat).metadata["revision"]
    _new(store, "Late child", "task", feat)
    assert store.get(feat).metadata["revision"] == before + 1


def test_revision_counts_requirement_changes_only(store):
    item = _new(store, "Item")
    dep = _new(store, "Dep")

    def revision():
        return store.get(item).metadata["revision"]

    for change in (
        lambda: lifecycle.record(store, item, "decision", "pytest", "why"),
        lambda: lifecycle.record(store, item, "progress", "pytest", "how far"),
        lambda: lifecycle.update_body(store, item, "New body"),
        lambda: lifecycle.set_dependencies(store, item, [dep]),
        lambda: lifecycle.convert(store, item, "feat"),
    ):
        before = revision()
        change()
        assert revision() == before + 1
    lifecycle.create(store, "Sibling", "task", None, project="TM")
    before = revision()
    for change in (
        lambda: lifecycle.record(store, item, "handoff", "pytest", "next"),
        lambda: lifecycle.record(store, item, "summary", "pytest", "what"),
        lambda: lifecycle.record(store, item, "verification", "pytest", "ok", "pass"),
        lambda: lifecycle.assign(store, item, "codex"),
        lambda: lifecycle.label(store, item, ["backend"], []),
        lambda: reorder_item(store, item, 3),
        lambda: lifecycle.finalize(store, item),
    ):
        change()
        assert revision() == before
    assert "rank" in _fields(store, item)


def test_closed_items_are_immutable(store):
    item = _new(store, "Item")
    sibling = _new(store, "Sibling")
    _closed(store, item)
    before = store.get(item)
    for change in (
        lambda: lifecycle.update_body(store, item, "new"),
        lambda: lifecycle.record(store, item, "decision", "pytest", "late"),
        lambda: lifecycle.record(store, item, "summary", "pytest", "late"),
        lambda: lifecycle.set_dependencies(store, item, []),
        lambda: lifecycle.edit(store, item, "T", "B", "why"),
        lambda: lifecycle.move(store, item, "backlog"),
        lambda: lifecycle.convert(store, item, "feat"),
        lambda: lifecycle.assign(store, item, "codex"),
        lambda: lifecycle.label(store, item, ["late"], []),
        lambda: reorder_item(store, item, 2),
    ):
        with pytest.raises((LifecycleError, StorageError), match="immutable"):
            change()
    assert store.get(item) == before
    assert store.sibling_order()["root"] == [item, sibling]


def test_edit_comments_the_reason(store):
    item = _new(store, "Item")
    edited = lifecycle.edit(store, item, "Renamed", "New body", "Clarify scope")
    assert (edited.title, edited.body) == ("Renamed", "New body")
    assert edited.metadata["journal"][-1]["text"] == "Clarify scope"
    with pytest.raises(LifecycleError, match="Explain why"):
        lifecycle.edit(store, item, "Renamed", "New body", " ")


def test_comments_cannot_change_or_be_removed(store):
    item = store.get(_new(store, "Item"))
    lifecycle.record(store, item.id, "decision", "pytest", "first")
    stored = store.get(item.id)
    altered = {**stored.metadata, "journal": [{**stored.metadata["journal"][0], "text": "rewritten"}]}
    with pytest.raises(StorageError, match="comments cannot change"):
        store.save(item.id, altered, stored.body)
    with pytest.raises(StorageError, match="comments cannot change"):
        store.save(item.id, {**stored.metadata, "journal": []}, stored.body)
    assert store.get(item.id).metadata["journal"] == stored.metadata["journal"]


# ---- assignee, labels, activity and last change ---------------------------


def test_assignee_and_labels(store):
    item = _new(store, "Item")
    assert lifecycle.assign(store, item, " claude-code ").metadata["assignee"] == "claude-code"
    with pytest.raises(NoChange):
        lifecycle.assign(store, item, "claude-code")
    assert lifecycle.assign(store, item, None).metadata["assignee"] is None
    with pytest.raises(LifecycleError, match="1-100"):
        lifecycle.assign(store, item, "x" * 101)
    assert lifecycle.label(store, item, ["ui", "backend"], []).metadata["labels"] == ["backend", "ui"]
    assert lifecycle.label(store, item, ["db"], ["ui"]).metadata["labels"] == ["backend", "db"]
    with pytest.raises(NoChange):
        lifecycle.label(store, item, ["db"], [])
    with pytest.raises(LifecycleError, match="label"):
        lifecycle.label(store, item, ["Bad Label"], [])
    # Labels keep code-point order whatever the database collation is.
    expected = ["a-c", "a_b", "ab", "backend", "db"]
    assert lifecycle.label(store, item, ["ab", "a_b", "a-c"], []).metadata["labels"] == expected
    assert store.get(item).metadata["labels"] == expected
    with pytest.raises(NoChange):
        lifecycle.label(store, item, ["a_b"], [])


def test_dependency_order_is_not_a_dependency_change(store):
    item, first, second = (_new(store, title) for title in ("Item", "First", "Second"))
    assert lifecycle.set_dependencies(store, item, [second, first]).depends_on == (first, second)
    lifecycle.set_dependencies(store, item, [second, first])
    assert _fields(store, item).count("dependencies") == 1


def test_each_field_change_writes_activity_and_comments_do_not(store):
    dep = _new(store, "Dep")
    item = _new(store, "Item")
    _new(store, "Sibling")
    lifecycle.edit(store, item, "Renamed", "New body", "why")
    lifecycle.finalize(store, item)
    lifecycle.set_dependencies(store, item, [dep])
    lifecycle.convert(store, item, "feat")
    lifecycle.assign(store, item, "codex")
    lifecycle.label(store, item, ["db"], [])
    reorder_item(store, item, 3)
    lifecycle.record(store, item, "progress", "pytest", "half")
    lifecycle.record(store, item, "handoff", "pytest", "next")
    _closed(store, dep)
    lifecycle.move(store, item, "active")
    assert _fields(store, item) == [
        "created", "title", "description", "draft", "dependencies", "type", "assignee", "labels", "rank", "status",
    ]
    entries = {entry["field"]: entry for entry in store.activity(item)}
    assert all(entry["author"] == "pytest" for entry in entries.values())
    assert (entries["title"]["old"], entries["title"]["new"]) == ("Item", "Renamed")
    assert (entries["description"]["old"], entries["description"]["new"]) == ("# Item\n", "New body")
    assert (entries["dependencies"]["old"], entries["dependencies"]["new"]) == ("", dep)
    assert (entries["status"]["old"], entries["status"]["new"]) == ("backlog", "active")


def test_last_change_time_follows_every_change(store):
    item = _new(store, "Item")
    dep = _new(store, "Dep")
    for change in (
        lambda: lifecycle.record(store, item, "handoff", "pytest", "next"),
        lambda: lifecycle.set_dependencies(store, item, [dep]),
        lambda: lifecycle.label(store, item, ["db"], []),
        lambda: lifecycle.finalize(store, item),
    ):
        before = store.get(item).modified
        time.sleep(0.01)
        change()
        assert store.get(item).modified > before
    before = store.get(item).modified
    store.items()
    store.document(item)
    assert store.get(item).modified == before


# ---- discard, order and consolidation -------------------------------------


def test_discard_only_unreferenced_draft_leaves_and_keys_are_not_reused(store):
    feat = _new(store, "Feature", "feat")
    child = _new(store, "Child", "task", feat)
    other = _new(store, "Other")
    lifecycle.set_dependencies(store, other, [child])
    lifecycle.label(store, child, ["db"], [])
    lifecycle.record(store, child, "decision", "pytest", "kept?")
    with pytest.raises(LifecycleError, match="only draft leaves"):
        lifecycle.discard(store, feat)
    with pytest.raises(LifecycleError, match="depends on this draft"):
        lifecycle.discard(store, child)
    lifecycle.set_dependencies(store, other, [])
    lifecycle.discard(store, child)
    assert store.get(child) is None
    for table in ("comments", "labels", "activity", "dependencies"):
        column = "depends_on" if table == "dependencies" else "item_id"
        assert store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} NOT IN (SELECT id FROM items)").fetchone()[0] == 0
    assert _new(store, "Next") == "TM-4"


def test_reorder_moves_only_the_open_item(store):
    ids = [_new(store, f"Item {n}") for n in range(4)]
    _closed(store, ids[1])
    ranks = {r[0]: r[1] for r in store.conn.execute("SELECT number, rank FROM items").fetchall()}
    assert reorder_item(store, ids[3], 1) == [ids[3], ids[0], ids[1], ids[2]]
    assert store.sibling_order()["root"] == [ids[3], ids[0], ids[1], ids[2]]
    assert reorder_item(store, ids[0], 4) == [ids[3], ids[1], ids[2], ids[0]]
    assert store.sibling_order()["root"] == [ids[3], ids[1], ids[2], ids[0]]
    after = {r[0]: r[1] for r in store.conn.execute("SELECT number, rank FROM items").fetchall()}
    assert [n for n in ranks if ranks[n] != after[n]] == [1, 4]  # TM-1 and TM-4 moved; done TM-2 kept its rank
    with pytest.raises(ValueError, match="between 1 and 4"):
        reorder_item(store, ids[0], 5)


def test_new_items_rank_after_their_siblings(store):
    ids = [_new(store, f"Item {n}") for n in range(11)]
    assert store.sibling_order()["root"] == ids  # TM-10 and TM-11 follow TM-9


def test_consolidation_is_not_available(store):
    done = _new(store, "Done")
    _closed(store, done)
    assert store.histories() == []
    with pytest.raises(StorageError, match="D8"):
        consolidate(store, [done], "Delivered")


# ---- transactions, schema and concurrency ---------------------------------


def test_failed_operation_leaves_the_database_unchanged(store):
    feat = _new(store, "Feature", "feat")
    before = (store.items(), store.activity(feat))
    with pytest.raises(RuntimeError):
        with store.lock():
            lifecycle.create(store, "Child", "task", feat)
            lifecycle.record(store, feat, "decision", "pytest", "half done")
            raise RuntimeError("stop")
    assert (store.items(), store.activity(feat)) == before
    assert _new(store, "After") == "TM-2"


def test_store_refuses_a_database_without_the_current_schema(empty_settings):
    with pytest.raises(StorageConfigError, match="taskman db upgrade"):
        open_sql_store(empty_settings, actor="pytest")


# Each worker connects first and then waits at the barrier, so the writes overlap.


def _create_many(settings: StorageSettings, prefix: str, count: int, barrier) -> None:
    store = open_sql_store(settings, actor=prefix)
    barrier.wait(30)
    for n in range(count):
        with store.lock():
            lifecycle.create(store, f"{prefix} {n}", "task", None, project="TM")


def _comment_many(settings: StorageSettings, item_id: str, prefix: str, count: int, barrier) -> None:
    store = open_sql_store(settings, actor=prefix)
    barrier.wait(30)
    for n in range(count):
        with store.lock():
            lifecycle.record(store, item_id, "decision", prefix, f"{prefix} {n}")


def _run(workers) -> None:
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
        assert worker.exitcode == 0


def test_concurrent_writers_get_distinct_keys(store, settings):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    _run([context.Process(target=_create_many, args=(settings, name, 15, barrier)) for name in ("left", "right")])
    keys = [item.id for item in store.items()]
    assert sorted(keys, key=lambda key: int(key.split("-")[1])) == [f"TM-{n}" for n in range(1, 31)]


def test_concurrent_comments_lose_no_updates(store, settings):
    item = _new(store, "Shared")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    _run([context.Process(target=_comment_many, args=(settings, item, name, 15, barrier)) for name in ("left", "right")])
    stored = store.get(item)
    journal = stored.metadata["journal"]
    assert len(journal) == 30
    assert stored.metadata["revision"] == 30
    assert sorted(entry["revision"] for entry in journal) == list(range(1, 31))
