"""Give a test item what the definition of ready needs, then finalize it.

``finalize`` needs a description beyond the title heading, a repository on the item
or an ancestor, and a passing readiness review for the current revision. Tests that
finalize items as setup use these helpers, so each test does not repeat those steps.
"""
from __future__ import annotations

from taskman import lifecycle

FIXTURE_REPOSITORY = "github.com/test/fixture"


def make_ready(store, key: str) -> None:
    """Add the missing parts of the definition of ready through the lifecycle."""
    item = store.get(key)
    if not lifecycle._has_description(item):
        lifecycle.update_body(store, key, f"# {item.title}\n\nFixture requirements.\n")
    items = {other.id: other for other in store.items()}
    if not items[key].metadata.get("repositories") and not lifecycle.inherited_repositories(items[key], items):
        lifecycle.set_repositories(store, key, [FIXTURE_REPOSITORY])
    lifecycle.record(store, key, "review", "pytest", "Fixture review: the checklist passes.", "pass")


def finalize(store, key: str):
    """``lifecycle.finalize`` for an item that the test made ready first."""
    make_ready(store, key)
    return lifecycle.finalize(store, key)
