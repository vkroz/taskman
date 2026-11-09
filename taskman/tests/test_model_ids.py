"""Tests for taskman.model.ids: today_prefix and allocate_id."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from taskman.model.ids import allocate_id, today_prefix


def test_today_prefix_format_for_known_date() -> None:
    assert today_prefix(date(2026, 5, 27)) == "260527"


def test_today_prefix_default_is_six_digits() -> None:
    p = today_prefix()
    assert len(p) == 6
    assert p.isdigit()


def test_allocate_id_on_empty_tree(tmp_path: Path) -> None:
    """First allocation on an empty tree returns nnn=000."""
    assert allocate_id(tmp_path, today=date(2026, 5, 27)) == "260527000"


def test_allocate_id_on_missing_tree(tmp_path: Path) -> None:
    """Non-existent tasks_dir still allocates from zero."""
    missing = tmp_path / "absent"
    assert allocate_id(missing, today=date(2026, 5, 27)) == "260527000"


def test_allocate_id_continues_after_existing(tmp_path: Path) -> None:
    """Allocator scans the tree and returns max+1, shared across types."""
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    epic_dir = backlog / "00.epic-260527000.backlog.foo"
    epic_dir.mkdir()
    (epic_dir / "_260527000.md").write_text("body")
    (backlog / "00.task-260527001.backlog.bar.md").write_text("body")
    assert allocate_id(tmp_path, today=date(2026, 5, 27)) == "260527002"


def test_allocate_id_tolerates_gaps(tmp_path: Path) -> None:
    """If 000 and 003 exist but 001/002 do not, next allocation is 004."""
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    (backlog / "00.task-260527000.backlog.a.md").write_text("body")
    (backlog / "00.task-260527003.backlog.b.md").write_text("body")
    assert allocate_id(tmp_path, today=date(2026, 5, 27)) == "260527004"


def test_allocate_id_ignores_other_days(tmp_path: Path) -> None:
    """IDs from other dates do not affect today's counter."""
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    (backlog / "00.task-260526999.backlog.a.md").write_text("body")
    assert allocate_id(tmp_path, today=date(2026, 5, 27)) == "260527000"


def test_allocate_id_scans_nested_items(tmp_path: Path) -> None:
    """IDs inside nested directories are counted, not just top-level."""
    backlog = tmp_path / "backlog"
    epic = backlog / "00.epic-260527000.backlog.root"
    feat = epic / "00.feat-260527001.backlog.child"
    feat.mkdir(parents=True)
    (epic / "_260527000.md").write_text("body")
    (feat / "_260527001.md").write_text("body")
    (feat / "00.task-260527005.backlog.grand.md").write_text("body")
    assert allocate_id(tmp_path, today=date(2026, 5, 27)) == "260527006"


def test_allocate_id_exhausted_raises(tmp_path: Path) -> None:
    """If 999 is allocated today, the next request raises RuntimeError."""
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    (backlog / "00.task-260527999.backlog.last.md").write_text("body")
    with pytest.raises(RuntimeError, match="exhausted"):
        allocate_id(tmp_path, today=date(2026, 5, 27))
