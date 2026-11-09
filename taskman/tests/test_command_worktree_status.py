"""Tests for the worktree-status command (parser + per-worktree aggregation)."""
from __future__ import annotations

from pathlib import Path

from taskman.commands.worktree_status import (
    _Worktree,
    _active_items,
    parse_porcelain,
)


# ---- parser ----


def test_parse_porcelain_empty() -> None:
    assert parse_porcelain("") == []


def test_parse_porcelain_single_worktree() -> None:
    text = "worktree /path/to/main\nHEAD abc\nbranch refs/heads/main\n"
    out = parse_porcelain(text)
    assert out == [_Worktree(path="/path/to/main", branch="main")]


def test_parse_porcelain_multi_worktree() -> None:
    text = (
        "worktree /a\nHEAD x\nbranch refs/heads/main\n\n"
        "worktree /b\nHEAD y\nbranch refs/heads/feature\n\n"
    )
    out = parse_porcelain(text)
    assert out == [
        _Worktree(path="/a", branch="main"),
        _Worktree(path="/b", branch="feature"),
    ]


def test_parse_porcelain_detached_head() -> None:
    text = "worktree /a\nHEAD abc\ndetached\n"
    out = parse_porcelain(text)
    assert out == [_Worktree(path="/a", branch=None)]


def test_parse_porcelain_non_refs_branch() -> None:
    """A branch line not under refs/heads is preserved verbatim."""
    text = "worktree /a\nHEAD x\nbranch refs/remotes/origin/main\n"
    out = parse_porcelain(text)
    assert out[0].branch == "refs/remotes/origin/main"


# ---- aggregation ----


def test_active_items_empty_dir(tmp_path: Path) -> None:
    assert _active_items(tmp_path) == []


def test_active_items_missing_dir(tmp_path: Path) -> None:
    assert _active_items(tmp_path / "nope") == []


def test_active_items_picks_only_active(tmp_path: Path) -> None:
    """Items whose in-name status is 'active' are included; others skipped."""
    (tmp_path / "backlog").mkdir()
    (tmp_path / "active").mkdir()
    (tmp_path / "done").mkdir()
    (tmp_path / "backlog" / "00.task-260527001.backlog.bl.md").write_text(
        "---\nid: '260527001'\ntype: task\nstatus: backlog\npriority: '00'\nslug: bl\ntitle: BL\n---\nbody\n"
    )
    (tmp_path / "active" / "00.task-260527002.active.act.md").write_text(
        "---\nid: '260527002'\ntype: task\nstatus: active\npriority: '00'\nslug: act\ntitle: Active One\n---\nbody\n"
    )
    (tmp_path / "done" / "00.task-260527003.done.dn.md").write_text(
        "---\nid: '260527003'\ntype: task\nstatus: done\npriority: '00'\nslug: dn\ntitle: Done\n---\nbody\n"
    )
    items = _active_items(tmp_path)
    assert len(items) == 1
    assert items[0]["id"] == "260527002"
    assert items[0]["title"] == "Active One"


def test_active_items_includes_nested_active_under_other_root(tmp_path: Path) -> None:
    """An item whose own name says 'active' counts, regardless of which folder it's in."""
    (tmp_path / "backlog").mkdir()
    epic_dir = tmp_path / "backlog" / "00.epic-260527000.backlog.e"
    epic_dir.mkdir()
    (epic_dir / "_260527000.md").write_text(
        "---\nid: '260527000'\ntype: epic\nstatus: backlog\npriority: '00'\nslug: e\ntitle: E\n---\nbody\n"
    )
    # Nested child marked active even though parent is backlog.
    (epic_dir / "00.task-260527004.active.nested.md").write_text(
        "---\nid: '260527004'\ntype: task\nstatus: active\npriority: '00'\nslug: nested\ntitle: Nested Active\n---\nbody\n"
    )
    items = _active_items(tmp_path)
    ids = {i["id"] for i in items}
    assert "260527004" in ids
