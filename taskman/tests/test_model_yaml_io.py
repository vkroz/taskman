"""Tests for taskman.model.yaml_io: the frontmatter emitter of `taskman show`."""
from __future__ import annotations

from taskman.model.yaml_io import emit_frontmatter


def test_emit_frontmatter_fences_data_and_body() -> None:
    text = emit_frontmatter({"id": "TM-1", "labels": ["db", "ui"]}, "# Body\n")
    assert text == "---\nid: TM-1\nlabels:\n  - db\n  - ui\n---\n\n# Body\n"


def test_emit_frontmatter_keeps_key_order() -> None:
    text = emit_frontmatter({"title": "T", "id": "TM-2", "draft": True}, "")
    assert text.splitlines()[1:4] == ["title: T", "id: TM-2", "draft: true"]
