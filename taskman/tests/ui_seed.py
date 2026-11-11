"""A seeded workspace for UI and API tests: three projects with drafts, dependencies and evidence.

Keys on a new database: TM-1..TM-13, RES-1..RES-13 and DIV-1..DIV-2. On this seed the
views hold: Attention RES-4; Active 7; Ready RES-5, RES-7, RES-9, RES-11, RES-13;
Blocked TM-8..TM-11; Drafts TM-12, TM-13, RES-6, RES-8, RES-10, RES-12; Backlog 15; Done 6.
"""
from __future__ import annotations

from taskman import lifecycle as L
from taskman.storage.store import Store


def seed(store: Store) -> None:
    """Fill an empty store. ``store.actor`` is the human who creates the items."""

    def mk(title, typ, parent=None, project=None, body=None, final=True):
        with store.lock():
            item = L.create(store, title, typ, parent, body, project=project)
            if final:
                L.finalize(store, item.id)
        return item.id

    def done(key, actor="Claude Code"):
        with store.lock():
            L.record(store, key, "summary", actor, "Implemented and documented. Changed files listed in the PR.")
            L.record(store, key, "verification", "Codex", "uv run pytest -q: 259 passed. Manual check of the UI flow.", "pass")
            L.close(store, key)

    def act(key):
        with store.lock():
            L.move(store, key, "active")

    epic = mk("Store work items in a SQL database", "epic", project="TM", body=(
        "## Outcome\nTaskman stores work items in SQLite or Postgres.\n\n## Requirements\n- R1 Lifecycle preservation\n"
        "- R2 Backend selection\n\n## Acceptance criteria\n- Contract suite passes on every backend.\n"))
    f1 = mk("Route all commands through a storage interface", "feat", epic)
    for title in ("Read work items through the storage interface", "Write work items and enforce lifecycle rules",
                  "Route the workspace UI through the interface"):
        key = mk(title, "task", f1)
        act(key)
        done(key)
    done(f1)
    f2 = mk("Serve Taskman over HTTP with a remote backend", "feat", epic, body=(
        "## Outcome\n`taskman` runs as an HTTPS server.\n\n## Requirements\n- Token on every API call.\n"
        "- Workspace UI over the server.\n"))
    act(epic)
    act(f2)
    t1 = mk("Add the operation registry", "task", f2)
    act(t1)
    t2 = mk("Add the remote backend client", "task", f2)
    t3 = mk("Serve the API and the UI with token login", "task", f2)
    t4 = mk("Prove remote backend acceptance", "task", f2)
    with store.lock():
        L.set_dependencies(store, t2, [t1])
        L.set_dependencies(store, t3, [t1])
        L.set_dependencies(store, t4, [t2, t3])
        L.assign(store, t1, "Claude Code")
        L.assign(store, t2, "Codex")
        L.label(store, t1, ["backend", "api"], [])
        L.label(store, t3, ["ui", "security"], [])
        L.record(store, t1, "decision", "Claude Code", "Forward at lifecycle-operation granularity so rules run on the server.")
        L.record(store, t1, "progress", "Claude Code", "Registry and decorator done; 13 operations registered.")
    f3 = mk("Deploy the server to Cloud Run with Cloud SQL", "feat", epic)
    with store.lock():
        L.set_dependencies(store, f3, [f2])
    mk("Write the deploy document", "task", f3, final=False)
    mk("Rename the --kind option", "task", project="TM", final=False)

    billing = mk("Billing reconciliation", "epic", project="RES", body="Reconcile cloud bills against usage.")
    cur = mk("Import AWS CUR files", "feat", billing)
    act(billing)
    act(cur)
    manifest = mk("Parse CUR manifest", "task", cur)
    act(manifest)
    done(manifest, "Codex")
    columns = mk("Handle missing columns in CUR 2.0", "task", cur)
    act(columns)
    with store.lock():
        L.assign(store, columns, "Vlad")
        L.label(store, columns, ["bug", "aws"], [])
        L.record(store, columns, "handoff", "Codex", "Blocked on sample file from customer; next agent should mock the schema.")
        L.record(store, columns, "verification", "Claude Code",
                 "pytest tests/test_cur.py: 2 failed (missing column lineItem/TaxType).", "fail")
    wizard = mk("Show onboarding wizard validation errors", "task", project="RES", body=(
        "## Reproduction\n1. Open wizard\n2. Submit empty role ARN\n\n## Expected\nInline error.\n\n"
        "## Actual\nSpinner never stops."))
    with store.lock():
        L.label(store, wizard, ["bug", "ui"], [])
    for n in range(1, 9):
        mk(f"Backlog idea {n}: improve cost explorer filters", "task", project="RES", final=(n % 2 == 0))
    pilot = mk("Diviner pilot", "epic", project="DIV")
    review = mk("Read-only review of query planner", "task", pilot)
    act(pilot)
    act(review)
    done(review)
