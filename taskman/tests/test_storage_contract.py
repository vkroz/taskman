"""Read contract every storage backend must satisfy.

Each backend supplies a seed function that builds the same scenario and
returns its IDs. The test cases use only the Store interface, so a new
backend adds a seed function and a parameter, not new cases.

Scenario:
    epic E
      feat F
        task T  (depends on D, body "Contract body text.", one decision)
    task D      (moved first among top-level items, closed, consolidated)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.storage import open_store
from tests.workflow_helpers import prepare

runner = CliRunner()


def _seed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    tasks_dir = tmp_path / "tasks"
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tasks_dir))
    monkeypatch.delenv("TASKMAN_BACKEND", raising=False)

    def run(*args: str, stdin: str | None = None) -> str:
        result = runner.invoke(app, list(args), input=stdin)
        assert result.exit_code == 0, result.output
        return result.output

    def new(title: str, item_type: str, parent: str | None = None) -> str:
        args = ["new", "--title", title, "--type", item_type, "--json"]
        if parent:
            args += ["--parent", parent]
        return json.loads(run(*args))["id"]

    ids = {"E": new("Epic", "epic")}
    ids["F"] = new("Feature", "feat", ids["E"])
    ids["T"] = new("Task", "task", ids["F"])
    ids["D"] = new("Dependency", "task")
    run("depend", ids["T"], "--on", ids["D"])
    run("update", ids["T"], "--body-file", "-", stdin="Contract body text.\n")
    run("record", ids["T"], "--kind", "decision", "--actor", "pytest", "--body-file", "-", stdin="Chose X.\n")
    run("reorder", ids["D"], "--position", "1")
    prepare(tasks_dir, ids["D"])
    run("close", ids["D"])
    run("consolidate", ids["D"], "--title", "Delivered dependency")
    return ids


SEEDS = {"file": _seed_file}


@pytest.fixture(params=sorted(SEEDS))
def seeded(request, tmp_path, monkeypatch):
    ids = SEEDS[request.param](tmp_path, monkeypatch)
    return open_store(), ids


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
    json.dumps(metadata, default=str)
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


def test_sibling_order_keeps_saved_positions(seeded):
    store, ids = seeded
    assert store.sibling_order()["root"][0] == ids["D"]


def test_unreadable_record_is_reported_not_raised(tmp_path, monkeypatch):
    """File backend only: other backends cannot hold a malformed record."""
    tasks_dir = tmp_path / "tasks"
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
    monkeypatch.setenv("TASKMAN_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("TASKMAN_BACKEND", "nosuch")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 2
    assert "Unknown storage backend 'nosuch'" in result.output
    assert "Supported: file" in result.output
