"""Tests for taskman.model.layout: status folders, parents, enumeration, lookup."""
from __future__ import annotations

from pathlib import Path

from taskman.model.layout import (
    STATUS_FOLDERS,
    find_item_by_id,
    is_directory_item,
    is_status_folder,
    is_top_level_item,
    iter_work_items,
    parent_item_path,
    special_file_path,
)


def _mk_tree(tmp_path: Path) -> None:
    """Build:

    tmp_path/
      backlog/
        00.task-260527001.backlog.leaf.md
      active/
        00.epic-260527000.active.taskman_python/
          _260527000.md
          00.feat-260527002.active.core/
            _260527002.md
            00.task-260527003.done.scaffold.md
    """
    (tmp_path / "backlog").mkdir()
    (tmp_path / "backlog" / "00.task-260527001.backlog.leaf.md").write_text("body")

    active = tmp_path / "active"
    epic_dir = active / "00.epic-260527000.active.taskman_python"
    feat_dir = epic_dir / "00.feat-260527002.active.core"
    feat_dir.mkdir(parents=True)
    (epic_dir / "_260527000.md").write_text("epic body")
    (feat_dir / "_260527002.md").write_text("feat body")
    (feat_dir / "00.task-260527003.done.scaffold.md").write_text("task body")


def test_is_status_folder_positive() -> None:
    for s in STATUS_FOLDERS:
        assert is_status_folder(s)


def test_is_status_folder_negative() -> None:
    assert not is_status_folder("epics")
    assert not is_status_folder("00.task-260527001.backlog.foo")
    assert not is_status_folder("")


def test_special_file_path() -> None:
    p = special_file_path(Path("/some/dir"), "260527001")
    assert p == Path("/some/dir/_260527001.md")


def test_is_directory_item_for_dir(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    epic = tmp_path / "active" / "00.epic-260527000.active.taskman_python"
    assert is_directory_item(epic)


def test_is_directory_item_for_file(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    task = tmp_path / "backlog" / "00.task-260527001.backlog.leaf.md"
    assert not is_directory_item(task)


def test_is_top_level_item_top(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    epic = tmp_path / "active" / "00.epic-260527000.active.taskman_python"
    leaf = tmp_path / "backlog" / "00.task-260527001.backlog.leaf.md"
    assert is_top_level_item(epic, tmp_path)
    assert is_top_level_item(leaf, tmp_path)


def test_is_top_level_item_nested(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    feat = tmp_path / "active" / "00.epic-260527000.active.taskman_python" / "00.feat-260527002.active.core"
    task = feat / "00.task-260527003.done.scaffold.md"
    assert not is_top_level_item(feat, tmp_path)
    assert not is_top_level_item(task, tmp_path)


def test_parent_item_path_top_level_returns_none(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    epic = tmp_path / "active" / "00.epic-260527000.active.taskman_python"
    assert parent_item_path(epic, tmp_path) is None


def test_parent_item_path_nested_returns_parent(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    epic = tmp_path / "active" / "00.epic-260527000.active.taskman_python"
    feat = epic / "00.feat-260527002.active.core"
    task = feat / "00.task-260527003.done.scaffold.md"
    assert parent_item_path(feat, tmp_path) == epic
    assert parent_item_path(task, tmp_path) == feat


def test_iter_work_items_full_tree(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    names = sorted(p.name for p in iter_work_items(tmp_path))
    assert names == [
        "00.epic-260527000.active.taskman_python",
        "00.feat-260527002.active.core",
        "00.task-260527001.backlog.leaf.md",
        "00.task-260527003.done.scaffold.md",
    ]


def test_iter_work_items_skips_special_files(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    # _<id>.md files must not appear.
    for p in iter_work_items(tmp_path):
        assert not p.name.startswith("_")


def test_iter_work_items_skips_status_folders(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    yielded_names = [p.name for p in iter_work_items(tmp_path)]
    assert "backlog" not in yielded_names
    assert "active" not in yielded_names


def test_iter_work_items_on_empty_tree(tmp_path: Path) -> None:
    assert list(iter_work_items(tmp_path)) == []


def test_iter_work_items_missing_dir(tmp_path: Path) -> None:
    assert list(iter_work_items(tmp_path / "nope")) == []


def test_find_item_by_id_existing(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    epic = find_item_by_id(tmp_path, "260527000")
    assert epic is not None
    assert epic.name == "00.epic-260527000.active.taskman_python"

    task = find_item_by_id(tmp_path, "260527003")
    assert task is not None
    assert task.name == "00.task-260527003.done.scaffold.md"


def test_find_item_by_id_missing(tmp_path: Path) -> None:
    _mk_tree(tmp_path)
    assert find_item_by_id(tmp_path, "999999999") is None
