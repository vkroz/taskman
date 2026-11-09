"""Tests for the migrate command — legacy bash layout → new uniform layout."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands.migrate import _derive_slug_from_filename
from taskman.model.names import parse_name
from taskman.model.yaml_io import read_file

runner = CliRunner()


# ---- slug derivation ----


def test_derive_slug_basic() -> None:
    assert _derive_slug_from_filename("20260525_foo.md") == "foo"


def test_derive_slug_with_collision_suffix() -> None:
    assert _derive_slug_from_filename("20260525_02_foo_bar.md") == "foo_bar"


def test_derive_slug_no_date_prefix() -> None:
    assert _derive_slug_from_filename("not_dated.md") == ""


# ---- fixture builders ----


def _write_legacy(path: Path, fm: dict[str, object], body: str = "body\n") -> None:
    """Write a legacy markdown file with the given YAML frontmatter and body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [f"{k}: {v}" for k, v in fm.items()]
    content = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body
    path.write_text(content, encoding="utf-8")


def _build_legacy_tree(source: Path) -> None:
    """Build a synthetic legacy tree covering every shape the AC names."""
    # Ad-hoc task (no feature, no epic).
    _write_legacy(
        source / "backlog" / "20260525_orphan_task.md",
        {"status": "backlog", "kind": "task", "slug": "orphan_task", "title": "Orphan"},
    )
    # Ad-hoc bug (no feature, kind=bug).
    _write_legacy(
        source / "active" / "20260525_ad_hoc_bug.md",
        {"status": "active", "kind": "bug", "slug": "ad_hoc_bug", "title": "A Bug"},
    )
    # Stand-alone feature with 2 tasks.
    _write_legacy(
        source / "features" / "20260525_lonely_feature.md",
        {"status": "backlog", "slug": "lonely_feature", "title": "Lonely Feature"},
    )
    _write_legacy(
        source / "backlog" / "20260525_lf_t1.md",
        {
            "status": "backlog",
            "kind": "task",
            "feature": "lonely_feature",
            "slug": "lf_t1",
            "title": "LF Task 1",
        },
    )
    _write_legacy(
        source / "done" / "20260525_lf_t2.md",
        {
            "status": "done",
            "kind": "task",
            "feature": "lonely_feature",
            "slug": "lf_t2",
            "title": "LF Task 2",
        },
    )
    # Epic with one feature with one task.
    _write_legacy(
        source / "epics" / "20260526_big_epic.md",
        {"status": "active", "slug": "big_epic", "title": "Big Epic"},
    )
    _write_legacy(
        source / "features" / "20260526_be_feature.md",
        {
            "status": "active",
            "epic": "big_epic",
            "slug": "be_feature",
            "title": "BE Feature",
        },
    )
    _write_legacy(
        source / "active" / "20260526_be_task.md",
        {
            "status": "active",
            "kind": "task",
            "feature": "be_feature",
            "slug": "be_task",
            "title": "BE Task",
        },
    )


# ---- happy paths ----


def test_migrate_runs_to_completion(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    r = runner.invoke(app, ["migrate", str(src), str(dest)])
    assert r.exit_code == 0, r.output
    assert "Migrated 8 item(s)" in r.output


def test_migrate_produces_correct_top_level_layout(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest)])
    # status folders exist
    for s in ("backlog", "active", "done"):
        assert (dest / s).exists()
    # orphan_task is at top of backlog/
    backlog_items = [p.name for p in (dest / "backlog").iterdir()]
    assert any("orphan_task" in n for n in backlog_items)
    assert any("lonely_feature" in n for n in backlog_items)
    # big_epic is at top of active/
    active_items = [p.name for p in (dest / "active").iterdir()]
    assert any("big_epic" in n for n in active_items)


def test_migrate_assigns_unique_yymmdd_ids(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest)])
    report = json.loads((dest / "_migration_report.json").read_text())
    ids = [entry["new_id"] for entry in report.values()]
    # 8 items, all unique 9-digit IDs.
    assert len(ids) == 8
    assert len(set(ids)) == 8
    for id_ in ids:
        assert len(id_) == 9
        assert id_.isdigit()
    # IDs starting with 260525 (5 items) and 260526 (3 items).
    by_prefix: dict[str, list[str]] = {}
    for id_ in ids:
        by_prefix.setdefault(id_[:6], []).append(id_)
    assert len(by_prefix["260525"]) == 5
    assert len(by_prefix["260526"]) == 3


def test_migrate_preserves_parent_child_relationships(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest)])
    # The epic should be a directory; its feature should sit inside; the task
    # should sit inside the feature.
    epics = [p for p in (dest / "active").iterdir() if "big_epic" in p.name]
    assert len(epics) == 1
    epic_dir = epics[0]
    assert epic_dir.is_dir()
    feats = [p for p in epic_dir.iterdir() if "be_feature" in p.name and p.is_dir()]
    assert len(feats) == 1
    feat_dir = feats[0]
    tasks = [p for p in feat_dir.iterdir() if "be_task" in p.name]
    assert len(tasks) == 1


def test_migrate_rewrites_frontmatter_schema(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest)])
    # Find any one of the new task files and check the schema.
    orphan = next(p for p in (dest / "backlog").iterdir() if "orphan_task" in p.name)
    data, body = read_file(orphan)
    assert data["id"]
    assert data["type"] == "task"
    assert data["status"] == "backlog"
    assert data["priority"] == "00"
    assert data["slug"] == "orphan_task"
    assert data["title"] == "Orphan"
    # epic/feature/kind dropped.
    assert "epic" not in data
    assert "feature" not in data
    assert "kind" not in data


def test_migrate_copies_body_verbatim(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write_legacy(
        src / "backlog" / "20260525_body_test.md",
        {"status": "backlog", "kind": "task", "slug": "body_test", "title": "BT"},
        body="custom body\n\n## section\n\ncontent here\n",
    )
    runner.invoke(app, ["migrate", str(src), str(dest)])
    p = next((dest / "backlog").iterdir())
    _, body = read_file(p)
    assert "custom body" in body
    assert "## section" in body
    assert "content here" in body


def test_migrate_writes_report(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest)])
    report_path = dest / "_migration_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert len(report) == 8
    for src_rel, entry in report.items():
        assert "new_path" in entry
        assert "new_id" in entry


def test_migrate_is_deterministic(tmp_path: Path) -> None:
    """Two runs against fresh dests produce byte-identical reports."""
    src = tmp_path / "src"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest1)])
    runner.invoke(app, ["migrate", str(src), str(dest2)])
    r1 = (dest1 / "_migration_report.json").read_text()
    r2 = (dest2 / "_migration_report.json").read_text()
    assert r1 == r2


def test_migrate_output_validates_clean(tmp_path: Path) -> None:
    """Migration output passes `taskman validate` end-to-end."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build_legacy_tree(src)
    runner.invoke(app, ["migrate", str(src), str(dest)])
    r = runner.invoke(app, ["validate"], env={"TASKMAN_TASKS_DIR": str(dest)})
    assert r.exit_code == 0, r.output


# ---- error / edge cases ----


def test_migrate_rejects_nonexistent_source(tmp_path: Path) -> None:
    r = runner.invoke(app, ["migrate", str(tmp_path / "nope"), str(tmp_path / "dest")])
    assert r.exit_code != 0
    assert "source is not a directory" in r.output


def test_migrate_rejects_non_empty_dest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "existing.txt").write_text("hi")
    r = runner.invoke(app, ["migrate", str(src), str(dest)])
    assert r.exit_code != 0
    assert "not empty" in r.output


def test_migrate_bash_task_without_slug_in_frontmatter(tmp_path: Path) -> None:
    """Bash taskman omitted `slug:` from task YAML; migration derives from filename."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    # No slug field — only the bash-defaults: status, kind, feature?, title.
    _write_legacy(
        src / "backlog" / "20260525_bash_style.md",
        {"status": "backlog", "kind": "task", "title": "Bash Style Task"},
    )
    r = runner.invoke(app, ["migrate", str(src), str(dest)])
    assert r.exit_code == 0, r.output
    out = next((dest / "backlog").iterdir())
    wi = parse_name(out.name)
    assert wi.slug == "bash_style"
    data, _ = read_file(out)
    assert data["slug"] == "bash_style"


def test_migrate_empty_source(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    r = runner.invoke(app, ["migrate", str(src), str(dest)])
    assert r.exit_code == 0
    assert "no items" in r.output
    assert (dest / "_migration_report.json").exists()
