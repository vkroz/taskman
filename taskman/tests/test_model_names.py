"""Tests for taskman.model.names: parse, emit, charset checks, special file."""
from __future__ import annotations

import pytest

from taskman.model.names import (
    NameParseError,
    WorkItemName,
    emit_name,
    name_is_special_file,
    parse_name,
    special_file_name,
)


# ---- parse ----

def test_parse_file_name() -> None:
    wi = parse_name("00.feat-260527001.backlog.workitem_model.md")
    assert wi == WorkItemName(
        priority="00",
        item_id="260527001",
        item_type="feat",
        status="backlog",
        slug="workitem_model",
    )


def test_parse_directory_name() -> None:
    wi = parse_name("00.epic-260527000.active.taskman_python")
    assert wi.priority == "00"
    assert wi.item_id == "260527000"
    assert wi.item_type == "epic"
    assert wi.status == "active"
    assert wi.slug == "taskman_python"


def test_parse_slug_with_digits_and_underscores() -> None:
    wi = parse_name("00.task-260527001.done.fix_bug_42.md")
    assert wi.slug == "fix_bug_42"


# ---- emit ----

def test_emit_file_form() -> None:
    wi = WorkItemName("00", "260527001", "task", "done", "port_cli")
    assert emit_name(wi, as_directory=False) == "00.task-260527001.done.port_cli.md"


def test_emit_directory_form() -> None:
    wi = WorkItemName("00", "260527000", "epic", "active", "taskman_python")
    assert emit_name(wi, as_directory=True) == "00.epic-260527000.active.taskman_python"


# ---- round-trip ----

@pytest.mark.parametrize(
    "name",
    [
        "00.feat-260527001.backlog.workitem_model.md",
        "00.task-260527003.done.scaffold.md",
        "99.epic-260101000.active.x.md",
    ],
)
def test_round_trip_file(name: str) -> None:
    assert emit_name(parse_name(name), as_directory=False) == name


@pytest.mark.parametrize(
    "name",
    [
        "00.feat-260527001.backlog.workitem_model",
        "00.epic-260527000.active.taskman_python",
    ],
)
def test_round_trip_directory(name: str) -> None:
    assert emit_name(parse_name(name), as_directory=True) == name


# ---- parse errors ----

def test_parse_rejects_wrong_field_count() -> None:
    with pytest.raises(NameParseError, match="4 dot-separated fields"):
        parse_name("00.feat-260527001.backlog.md")


def test_parse_rejects_too_many_fields() -> None:
    with pytest.raises(NameParseError, match="4 dot-separated fields"):
        parse_name("00.feat-260527001.backlog.foo.bar.md")


def test_parse_rejects_missing_type_separator() -> None:
    with pytest.raises(NameParseError, match="<type>-<id>"):
        parse_name("00.260527001.backlog.foo.md")


def test_parse_rejects_bad_slug_charset_uppercase() -> None:
    with pytest.raises(NameParseError, match="slug"):
        parse_name("00.feat-260527001.backlog.Workitem.md")


def test_parse_rejects_bad_slug_charset_hyphen() -> None:
    with pytest.raises(NameParseError, match="slug"):
        parse_name("00.feat-260527001.backlog.work-item.md")


def test_parse_rejects_bad_status() -> None:
    with pytest.raises(NameParseError, match="status"):
        parse_name("00.feat-260527001.deferred.foo.md")


def test_parse_rejects_bad_type() -> None:
    with pytest.raises(NameParseError, match="item_type"):
        parse_name("00.story-260527001.backlog.foo.md")


def test_parse_rejects_bad_id_too_short() -> None:
    with pytest.raises(NameParseError, match="item_id"):
        parse_name("00.feat-26052701.backlog.foo.md")  # 8 digits


def test_parse_rejects_bad_id_non_numeric() -> None:
    with pytest.raises(NameParseError, match="item_id"):
        parse_name("00.feat-26052700X.backlog.foo.md")


def test_parse_rejects_bad_priority_non_numeric() -> None:
    with pytest.raises(NameParseError, match="priority"):
        parse_name("X0.feat-260527001.backlog.foo.md")


def test_parse_rejects_bad_priority_too_short() -> None:
    with pytest.raises(NameParseError, match="priority"):
        parse_name("0.feat-260527001.backlog.foo.md")


# ---- special file ----

def test_special_file_name_uses_bare_id() -> None:
    assert special_file_name("260527001") == "_260527001.md"


def test_special_file_name_rejects_typed_id() -> None:
    """Bare ID only — survives type conversion without rename."""
    with pytest.raises(NameParseError):
        special_file_name("feat-260527000")


def test_special_file_name_rejects_bad_id() -> None:
    with pytest.raises(NameParseError):
        special_file_name("12345")


def test_name_is_special_file_positive() -> None:
    assert name_is_special_file("_260527001.md")


def test_name_is_special_file_negative_regular_item() -> None:
    assert not name_is_special_file("00.task-260527001.done.foo.md")


def test_name_is_special_file_negative_no_md_suffix() -> None:
    assert not name_is_special_file("_260527001")


# ---- dataclass validation ----

def test_workitem_name_validates_in_constructor() -> None:
    with pytest.raises(NameParseError, match="status"):
        WorkItemName("00", "260527001", "task", "bogus", "foo")  # type: ignore[arg-type]
