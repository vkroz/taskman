"""Lifecycle cases for the file backend.

The SQL backends have their own contract suite in ``test_storage_contract.py``
for the new work-item model. These cases keep the file backend covered until
feature 261109005 removes it. Every case uses only ``taskman.lifecycle`` and
the ``Store`` interface.

Read scenario (``seeded``):
    epic E
      feat F
        task T  (depends on D, body "Contract body text.", one decision)
    task D      (moved first among top-level items, closed, consolidated)
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskman import lifecycle
from taskman.cli import app
from taskman.lifecycle import LifecycleError, NoChange
from taskman.model.collection import consolidate, histories
from taskman.model.ordering import reorder_item
from taskman.storage.store import Store, open_store

runner = CliRunner()


def _empty_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKMAN_BACKEND", "file")
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("TASKMAN_BACKEND", "file")


BACKENDS = {"file": _empty_file}


@pytest.fixture(params=sorted(BACKENDS))
def store(request, tmp_path, monkeypatch) -> Store:
    BACKENDS[request.param](tmp_path, monkeypatch)
    return open_store()


def _ready_to_close(store: Store, item_id: str) -> None:
    for kind, result in (("summary", None), ("verification", "pass")):
        lifecycle.record(store, item_id, kind, "pytest", f"{kind} text", result)


def _closed(store: Store, item_id: str) -> None:
    lifecycle.finalize(store, item_id)
    _ready_to_close(store, item_id)
    lifecycle.close(store, item_id)


@pytest.fixture
def seeded(store):
    ids = {"E": lifecycle.create(store, "Epic", "epic", None).id}
    ids["F"] = lifecycle.create(store, "Feature", "feat", ids["E"]).id
    ids["T"] = lifecycle.create(store, "Task", "task", ids["F"]).id
    ids["D"] = lifecycle.create(store, "Dependency", "task", None).id
    lifecycle.set_dependencies(store, ids["T"], [ids["D"]])
    lifecycle.update_body(store, ids["T"], "Contract body text.\n")
    lifecycle.record(store, ids["T"], "decision", "pytest", "Chose X.")
    reorder_item(store, ids["D"], 1)
    _closed(store, ids["D"])
    consolidate(store, [ids["D"]], "Delivered dependency")
    return store, ids


# ---- reads -------------------------------------------------------------


def test_get_returns_the_item_record(seeded):
    store, ids = seeded
    item = store.get(ids["T"])
    assert item is not None
    assert (item.id, item.type, item.status, item.parent_id) == (ids["T"], "task", "backlog", ids["F"])
    assert item.title == "Task"
    assert item.body.strip() == "Contract body text."
    assert item.draft is True
    assert item.depends_on == (ids["D"],)
    assert item.error is None


def test_get_unknown_id_returns_none(seeded):
    store, _ = seeded
    assert store.get("999999999") is None


def test_items_include_every_item_with_parent_links(seeded):
    store, ids = seeded
    parents = {item.id: item.parent_id for item in store.items()}
    assert parents == {ids["E"]: None, ids["F"]: ids["E"], ids["T"]: ids["F"], ids["D"]: None}


def test_metadata_is_plain_data_with_the_journal(seeded):
    store, ids = seeded
    metadata = store.get(ids["T"]).metadata
    assert [entry["kind"] for entry in metadata["journal"]] == ["decision"]
    assert metadata["journal"][0]["text"] == "Chose X."


def test_closed_item_reports_done(seeded):
    store, ids = seeded
    item = store.get(ids["D"])
    assert item.status == "done"
    assert item.metadata["status"] == "done"


def test_document_contains_frontmatter_and_body(seeded):
    store, ids = seeded
    text = store.document(ids["T"])
    assert "title: Task" in text
    assert "Contract body text." in text


def test_histories_list_consolidated_ids(seeded):
    store, ids = seeded
    records = store.histories()
    assert [(record["title"], record["ids"]) for record in records] == [("Delivered dependency", [ids["D"]])]
    assert records[0]["path"]
    assert ids["D"] in records[0]["summary"]


def test_sibling_order_keeps_saved_positions(seeded):
    store, ids = seeded
    assert store.sibling_order()["root"][0] == ids["D"]


# ---- creation and structure -------------------------------------------


def test_create_starts_as_a_backlog_draft(store):
    item = lifecycle.create(store, "Write docs", "task", None)
    assert (item.status, item.draft, item.type, item.title) == ("backlog", True, "task", "Write docs")
    assert item.body == "# Write docs\n"


def test_create_allocates_distinct_ids(store):
    ids = {lifecycle.create(store, f"Item {n}", "task", None).id for n in range(5)}
    assert len(ids) == 5


def test_task_cannot_have_children(store):
    task = lifecycle.create(store, "Leaf", "task", None)
    with pytest.raises(LifecycleError, match="task = leaf invariant"):
        lifecycle.create(store, "Child", "task", task.id)


def test_closed_parent_rejects_new_children(store):
    feat = lifecycle.create(store, "Feature", "feat", None)
    _closed(store, feat.id)
    with pytest.raises(LifecycleError, match="closed parents are immutable"):
        lifecycle.create(store, "Child", "task", feat.id)


def test_convert_to_task_requires_no_children(store):
    feat = lifecycle.create(store, "Feature", "feat", None)
    lifecycle.create(store, "Child", "task", feat.id)
    with pytest.raises(LifecycleError, match="has children"):
        lifecycle.convert(store, feat.id, "task")
    leaf = lifecycle.create(store, "Leaf", "task", None)
    assert lifecycle.convert(store, leaf.id, "feat").type == "feat"


# ---- dependencies and readiness ---------------------------------------


def test_dependencies_reject_unknown_ids_and_cycles(store):
    a = lifecycle.create(store, "A", "task", None).id
    b = lifecycle.create(store, "B", "task", None).id
    lifecycle.set_dependencies(store, a, [b])
    with pytest.raises(LifecycleError, match="cycle"):
        lifecycle.set_dependencies(store, b, [a])
    with pytest.raises(LifecycleError, match="not found"):
        lifecycle.set_dependencies(store, a, ["999999999"])


def test_child_cannot_depend_on_its_parent(store):
    feat = lifecycle.create(store, "Feature", "feat", None).id
    child = lifecycle.create(store, "Child", "task", feat).id
    with pytest.raises(LifecycleError, match="cycle"):
        lifecycle.set_dependencies(store, child, [feat])


def test_activation_requires_finalized_items_and_done_dependencies(store):
    dep = lifecycle.create(store, "Dep", "task", None).id
    work = lifecycle.create(store, "Work", "task", None).id
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

    feat = lifecycle.create(store, "Feature", "feat", None).id
    child = lifecycle.create(store, "Child", "task", feat).id
    dep = lifecycle.create(store, "Dep", "task", None).id
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


# ---- journal, revisions and closure -----------------------------------


def test_verification_entries_require_a_result(store):
    item = lifecycle.create(store, "Item", "task", None).id
    with pytest.raises(LifecycleError, match="verification requires"):
        lifecycle.record(store, item, "verification", "pytest", "ran checks")
    with pytest.raises(LifecycleError, match="unknown entry kind"):
        lifecycle.record(store, item, "note", "pytest", "text")


def test_close_requires_summary_and_passing_verification(store):
    item = lifecycle.create(store, "Item", "task", None).id
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
    feat = lifecycle.create(store, "Feature", "feat", None).id
    lifecycle.create(store, "Child", "task", feat)
    lifecycle.finalize(store, feat)
    _ready_to_close(store, feat)
    with pytest.raises(LifecycleError, match="descendants not in done"):
        lifecycle.close(store, feat)


def test_body_change_invalidates_earlier_evidence(store):
    item = lifecycle.create(store, "Item", "task", None).id
    lifecycle.finalize(store, item)
    _ready_to_close(store, item)
    lifecycle.update_body(store, item, "Changed requirements.\n")
    with pytest.raises(LifecycleError, match="current revision"):
        lifecycle.close(store, item)


def test_child_change_invalidates_parent_evidence(store):
    feat = lifecycle.create(store, "Feature", "feat", None).id
    child = lifecycle.create(store, "Child", "task", feat).id
    lifecycle.finalize(store, feat)
    _closed(store, child)
    _ready_to_close(store, feat)
    before = store.get(feat).metadata.get("revision", 0)
    lifecycle.create(store, "Late child", "task", feat)
    assert store.get(feat).metadata["revision"] == before + 1


def test_closed_items_are_immutable(store):
    item = lifecycle.create(store, "Item", "task", None).id
    _closed(store, item)
    for change in (
        lambda: lifecycle.update_body(store, item, "new"),
        lambda: lifecycle.record(store, item, "decision", "pytest", "late"),
        lambda: lifecycle.set_dependencies(store, item, []),
        lambda: lifecycle.edit(store, item, "T", "B", "why"),
        lambda: lifecycle.move(store, item, "backlog"),
        lambda: lifecycle.convert(store, item, "feat"),
    ):
        with pytest.raises(LifecycleError, match="immutable"):
            change()


def test_edit_journals_the_reason(store):
    item = lifecycle.create(store, "Item", "task", None).id
    edited = lifecycle.edit(store, item, "Renamed", "New body", "Clarify scope")
    assert edited.title == "Renamed"
    assert edited.body == "New body"
    assert edited.metadata["journal"][-1]["text"] == "Clarify scope"
    with pytest.raises(LifecycleError, match="Explain why"):
        lifecycle.edit(store, item, "Renamed", "New body", " ")


# ---- discard, order and consolidation ---------------------------------


def test_discard_only_unreferenced_draft_leaves(store):
    feat = lifecycle.create(store, "Feature", "feat", None).id
    child = lifecycle.create(store, "Child", "task", feat).id
    other = lifecycle.create(store, "Other", "task", None).id
    lifecycle.set_dependencies(store, other, [child])
    with pytest.raises(LifecycleError, match="only draft leaves"):
        lifecycle.discard(store, feat)
    with pytest.raises(LifecycleError, match="depends on this draft"):
        lifecycle.discard(store, child)
    lifecycle.set_dependencies(store, other, [])
    lifecycle.discard(store, child)
    assert store.get(child) is None
    lifecycle.discard(store, feat)
    assert store.get(feat) is None


def test_reorder_persists_sibling_positions(store):
    ids = [lifecycle.create(store, f"Item {n}", "task", None).id for n in range(3)]
    assert reorder_item(store, ids[2], 1) == [ids[2], ids[0], ids[1]]
    assert store.sibling_order()["root"] == [ids[2], ids[0], ids[1]]
    with pytest.raises(ValueError, match="between 1 and 3"):
        reorder_item(store, ids[0], 4)


def test_consolidation_requires_done_work_and_hides_nothing_twice(store):
    open_item = lifecycle.create(store, "Open", "task", None).id
    with pytest.raises(ValueError, match="must be done"):
        consolidate(store, [open_item], "Too early")
    done = lifecycle.create(store, "Done", "task", None).id
    _closed(store, done)
    record = consolidate(store, [done], "Delivered")
    assert histories(store) == [record]
    with pytest.raises(ValueError, match="already consolidated"):
        consolidate(store, [done], "Again")


# ---- concurrency -------------------------------------------------------


def _create_many(env: dict[str, str], prefix: str, count: int) -> None:
    import os

    os.environ.update(env)
    store = open_store()
    for n in range(count):
        with store.lock():
            lifecycle.create(store, f"{prefix} {n}", "task", None)


def test_concurrent_writers_lose_no_items(store, monkeypatch):
    import os

    env = {key: value for key, value in os.environ.items() if key.startswith("TASKMAN_")}
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_create_many, args=(env, name, 15)) for name in ("left", "right")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
        assert worker.exitcode == 0
    items = store.items()
    assert len(items) == 30
    assert len({item.id for item in items}) == 30


# ---- backend-specific --------------------------------------------------


def test_unreadable_record_is_reported_not_raised(tmp_path, monkeypatch):
    """File backend only: other backends cannot hold a malformed record."""
    tasks_dir = tmp_path / "tasks"
    monkeypatch.setenv("TASKMAN_BACKEND", "file")
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tasks_dir))
    (tasks_dir / "backlog").mkdir(parents=True)
    (tasks_dir / "backlog" / "00.task-261101002.backlog.badyaml.md").write_text("---\ntitle: [unclosed\n---\n\nbody\n")
    (tasks_dir / "backlog" / "00.task-261101003.backlog.nofm.md").write_text("no frontmatter\n")
    (tasks_dir / "backlog" / "00.epic-261101001.backlog.nobody").mkdir()
    items = {item.id: item for item in open_store().items()}
    assert set(items) == {"261101001", "261101002", "261101003"}
    for item in items.values():
        assert item.error
        assert (item.metadata, item.body, item.title) == ({}, "", "")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "badyaml" in result.output


def test_unknown_backend_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_BACKEND", "file")
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("TASKMAN_BACKEND", "nosuch")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 2
    assert "Unknown storage backend 'nosuch'" in result.output
    assert "Supported: file" in result.output
