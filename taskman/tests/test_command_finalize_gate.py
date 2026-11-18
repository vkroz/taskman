"""`finalize` applies the definition of ready, through a local database and through a server."""
from __future__ import annotations

import pytest

from tests.sql_helpers import comment, new, run, show


@pytest.fixture(params=["sqlite_env", "remote_env"])
def env(request):
    return request.getfixturevalue(request.param)


def _flat(output: str) -> str:
    """The text of a usage error without the box lines and wrapping."""
    return " ".join(output.replace("│", " ").split())


def test_a_review_needs_pass_or_fail(env):
    key = new("Item")
    assert "review requires --result pass|fail" in _flat(run("comment", key, "--kind", "review", "--body", "x", code=2))
    blocked = run("comment", key, "--kind", "review", "--result", "blocked", "--body", "x", code=2)
    assert "review requires --result pass|fail" in _flat(blocked)
    run("comment", key, "--kind", "review", "--result", "fail", "--body", "Outcome unclear.")
    assert show(key)["frontmatter"]["journal"][-1]["result"] == "fail"


def test_finalize_refuses_and_names_each_failed_check(env):
    key = new("Item")
    output = run("finalize", key, code=2)
    for text in (f"cannot finalize {key}", "Description is empty", "No repository on the item or an ancestor",
                 "Readiness review for rev 0: missing"):
        assert text in output
    assert show(key)["frontmatter"]["draft"] is True


def test_finalize_succeeds_when_the_definition_of_ready_is_met(env):
    key = new("Item", body="# Item\n\nThe outcome.\n")
    run("repo", key, "github.com/o/r")
    comment(key, "review", "The checklist passes.", result="pass")
    run("update", key, "--body-file", "-", input="# Item\n\nThe outcome, refined.\n")
    assert "stale at rev 1" in run("finalize", key, code=2)
    comment(key, "review", "The checklist passes.", result="pass")
    assert run("finalize", key).strip() == f"Finalized: {key}"
    assert show(key)["frontmatter"]["draft"] is False
    assert "not a draft" in run("finalize", key)
