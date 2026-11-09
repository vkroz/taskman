"""Tests for taskman.model.yaml_io: parse, emit, round-trip with comments."""
from __future__ import annotations

from pathlib import Path

import pytest

from taskman.model.yaml_io import (
    FrontmatterError,
    emit_frontmatter,
    parse_frontmatter,
    read_file,
    write_file,
)


def test_parse_basic_frontmatter() -> None:
    text = "---\nstatus: backlog\nslug: foo\n---\n\n# Body\n\nContent.\n"
    data, body = parse_frontmatter(text)
    assert data["status"] == "backlog"
    assert data["slug"] == "foo"
    assert body == "# Body\n\nContent.\n"


def test_parse_empty_frontmatter_dict() -> None:
    text = "---\n---\n\nbody\n"
    data, body = parse_frontmatter(text)
    assert data == {}
    assert body == "body\n"


def test_parse_rejects_missing_opening_fence() -> None:
    with pytest.raises(FrontmatterError, match="first line"):
        parse_frontmatter("not a fence\nstatus: backlog\n---\n")


def test_parse_rejects_unterminated_frontmatter() -> None:
    with pytest.raises(FrontmatterError, match="closing"):
        parse_frontmatter("---\nstatus: backlog\nno end fence here\n")


def test_emit_then_parse_round_trip() -> None:
    data = {"status": "active", "slug": "bar", "title": "Test"}
    body = "# Test\n\nBody content.\n"
    text = emit_frontmatter(data, body)
    data2, body2 = parse_frontmatter(text)
    assert data2["status"] == "active"
    assert data2["slug"] == "bar"
    assert data2["title"] == "Test"
    assert body2 == body


def test_round_trip_preserves_comments() -> None:
    """ruamel.yaml round-trip preserves YAML comments."""
    text = (
        "---\n"
        "# comment above status\n"
        "status: backlog\n"
        "slug: foo  # inline comment\n"
        "---\n\n"
        "body\n"
    )
    data, body = parse_frontmatter(text)
    re_emitted = emit_frontmatter(data, body)
    assert "# comment above status" in re_emitted
    assert "inline comment" in re_emitted


def test_round_trip_preserves_list_field() -> None:
    """List values (e.g. depends_on) round-trip cleanly."""
    text = "---\nstatus: backlog\ndepends_on: [260527000, 260527001]\n---\n\nbody\n"
    data, body = parse_frontmatter(text)
    assert data["depends_on"] == ["260527000", "260527001"] or data["depends_on"] == [
        260527000,
        260527001,
    ]
    re_emitted = emit_frontmatter(data, body)
    data2, _ = parse_frontmatter(re_emitted)
    assert data2["depends_on"] == data["depends_on"]


def test_read_write_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "t.md"
    path.write_text("---\nstatus: backlog\nslug: foo\n---\n\nbody\n")
    data, body = read_file(path)
    assert data["status"] == "backlog"
    data["status"] = "active"
    write_file(path, data, body)
    data2, body2 = read_file(path)
    assert data2["status"] == "active"
    assert body2 == body
