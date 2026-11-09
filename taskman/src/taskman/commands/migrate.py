"""`migrate` command — read the legacy bash-era layout, write the new one.

The legacy layout is flat:

    <source>/
      epics/      YYYYMMDD_<slug>.md   (status in YAML)
      features/   YYYYMMDD_<slug>.md   (status in YAML; may carry epic: <slug>)
      backlog/    YYYYMMDD[_NN]_<slug>.md   (status from folder; may carry feature:)
      active/     ...
      done/       ...

The new layout is nested under top-level status folders, with items
represented by directories (containing ``_<id>.md``) when they have
children and by single files otherwise. IDs are back-assigned from each
source file's ``YYYYMMDD`` prefix + a deterministic per-day sequence.

Outputs ``<dest>/_migration_report.json`` mapping every source path to
its new path and ID. Re-running against a fresh ``<dest>`` with the same
``<source>`` is byte-identical (deterministic).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from taskman.model.names import (
    WorkItemName,
    WorkItemStatus,
    WorkItemType,
    emit_name,
    special_file_name,
)
from taskman.model.yaml_io import emit_frontmatter, parse_frontmatter


_FENCE = "---"


def _tolerant_parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Line-based frontmatter parser tolerant of unquoted colons in values.

    The bash taskman emitted `title: ...` lines without quoting, so titles
    that contain a colon break strict YAML. For migration we only need
    scalar `key: value` pairs (status, slug, title, epic, feature, kind,
    draft), so a simple split-on-first-colon is robust and sufficient.
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != _FENCE:
        raise MigrationError(f"expected first line to be {_FENCE!r}")
    try:
        end = lines.index(_FENCE, 1)
    except ValueError as exc:
        raise MigrationError(f"no closing {_FENCE!r} found") from exc
    data: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        data[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return data, body

_DATE_RE = re.compile(r"^(\d{8})")
# Legacy task filename: YYYYMMDD[_NN]_<slug>.md — extract the slug.
_LEGACY_TASK_NAME_RE = re.compile(r"^\d{8}_(?:\d+_)?(.+)$")
_LEGACY_TYPE_MAP: dict[str, WorkItemType] = {
    "epic": "epic",
    "feature": "feat",
    "task": "task",
}
_LEGACY_STATUS_DIRS: tuple[str, ...] = ("backlog", "active", "done")


class MigrationError(ValueError):
    """User-visible migration error."""


@dataclass(slots=True)
class _Source:
    source_path: Path
    kind: str  # legacy: epic | feature | task
    status: WorkItemStatus
    slug: str
    title: str
    epic_ref: str | None
    feature_ref: str | None
    frontmatter: dict[str, Any]
    body: str
    date_prefix: str  # YYMMDD


def _extract_date_prefix(filename: str) -> str | None:
    """Extract YYMMDD from a YYYYMMDD-prefixed filename."""
    m = _DATE_RE.match(filename)
    if not m:
        return None
    return m.group(1)[2:]  # strip century


def _derive_slug_from_filename(filename: str) -> str:
    """Derive a slug from a legacy task filename like ``20260525[_NN]_slug.md``.

    Bash taskman did not write ``slug:`` into task frontmatter — it lived
    only in the filename. Strip the date prefix and optional ``_NN``
    collision suffix; what remains (minus ``.md``) is the slug.
    """
    stem = filename[:-3] if filename.endswith(".md") else filename
    m = _LEGACY_TASK_NAME_RE.match(stem)
    return m.group(1) if m else ""


def _classify(path: Path, source_root: Path) -> str | None:
    """Return 'epic' | 'feature' | 'task' from the path's containing folder."""
    rel = path.relative_to(source_root)
    if len(rel.parts) < 2:
        return None
    top = rel.parts[0]
    if top == "epics":
        return "epic"
    if top == "features":
        return "feature"
    if top in _LEGACY_STATUS_DIRS:
        return "task"
    return None


def _load_source(source_root: Path) -> list[_Source]:
    """Walk source_root and load every legacy work item."""
    items: list[_Source] = []
    for path in sorted(source_root.rglob("*.md")):
        if not path.is_file():
            continue
        kind = _classify(path, source_root)
        if kind is None:
            continue
        date_prefix = _extract_date_prefix(path.name)
        if date_prefix is None:
            raise MigrationError(f"no YYYYMMDD prefix on: {path}")
        try:
            data, body = _tolerant_parse_frontmatter(path.read_text(encoding="utf-8"))
        except MigrationError as exc:
            raise MigrationError(f"frontmatter parse failed for {path}: {exc}") from exc

        slug = data.get("slug", "").strip()
        if not slug:
            # Bash taskman omitted slug from task frontmatter; derive from filename.
            slug = _derive_slug_from_filename(path.name)
        if not slug:
            raise MigrationError(f"could not determine slug for: {path}")
        title = data.get("title", "").strip()
        if not title:
            title = slug.replace("_", " ").title()
        if kind == "task":
            status_str = path.parent.name
        else:
            status_str = data.get("status", "backlog").strip()
        if status_str not in ("backlog", "active", "done"):
            raise MigrationError(f"unknown status {status_str!r} in: {path}")

        # Normalize so frontmatter carries the derived/canonical values.
        data["slug"] = slug
        data["title"] = title
        data["status"] = status_str

        items.append(
            _Source(
                source_path=path,
                kind=kind,
                status=status_str,  # type: ignore[arg-type]
                slug=slug,
                title=title,
                epic_ref=data.get("epic", "").strip() or None,
                feature_ref=data.get("feature", "").strip() or None,
                frontmatter=dict(data),
                body=body,
                date_prefix=date_prefix,
            )
        )
    return items


def _allocate_ids(items: list[_Source]) -> dict[Path, str]:
    """Map source_path → YYMMDDnnn, deterministically by (date, source path)."""
    by_date: dict[str, list[_Source]] = {}
    for item in items:
        by_date.setdefault(item.date_prefix, []).append(item)
    id_map: dict[Path, str] = {}
    for date, group in by_date.items():
        group.sort(key=lambda i: str(i.source_path))
        for n, item in enumerate(group):
            if n > 999:
                raise MigrationError(
                    f"more than 1000 items share date {date}; counter exhausted"
                )
            id_map[item.source_path] = f"{date}{n:03d}"
    return id_map


def _build_parent_map(items: list[_Source]) -> dict[Path, Path | None]:
    """Resolve parent references via slug lookup. Unresolved refs → orphan (top-level)."""
    epics_by_slug = {item.slug: item.source_path for item in items if item.kind == "epic"}
    features_by_slug = {
        item.slug: item.source_path for item in items if item.kind == "feature"
    }
    parents: dict[Path, Path | None] = {}
    for item in items:
        if item.kind == "epic":
            parents[item.source_path] = None
        elif item.kind == "feature":
            parents[item.source_path] = (
                epics_by_slug.get(item.epic_ref) if item.epic_ref else None
            )
        else:  # task
            parents[item.source_path] = (
                features_by_slug.get(item.feature_ref) if item.feature_ref else None
            )
    return parents


def _compute_paths(
    items: list[_Source],
    dest: Path,
    id_map: dict[Path, str],
    parents: dict[Path, Path | None],
) -> dict[Path, Path]:
    """Return source_path → new on-disk path under dest."""
    children_count: dict[Path, int] = {}
    for item in items:
        p = parents.get(item.source_path)
        if p is not None:
            children_count[p] = children_count.get(p, 0) + 1
    has_children = set(children_count.keys())

    # Process in legacy-kind order: epics first (always roots), then features
    # (under epics), then tasks (under features or orphan top-level).
    order_key = {"epic": 0, "feature": 1, "task": 2}
    items_sorted = sorted(items, key=lambda i: (order_key[i.kind], str(i.source_path)))

    paths: dict[Path, Path] = {}
    for item in items_sorted:
        parent_src = parents.get(item.source_path)
        if parent_src is None:
            parent_dir = dest / item.status
        else:
            parent_dir = paths[parent_src]
        name_obj = WorkItemName(
            priority="00",
            item_id=id_map[item.source_path],
            item_type=_LEGACY_TYPE_MAP[item.kind],
            status=item.status,
            slug=item.slug,
        )
        as_dir = item.source_path in has_children
        paths[item.source_path] = parent_dir / emit_name(name_obj, as_directory=as_dir)
    return paths


def _rewrite_frontmatter(
    legacy: dict[str, Any], new_id: str, new_type: WorkItemType
) -> dict[str, Any]:
    """Translate legacy frontmatter to new schema.

    Adds ``id``, ``type``, ``priority``. Drops ``epic:``, ``feature:``,
    ``kind:`` (legacy bug/task distinction; bugs become tasks). Preserves
    other fields (``status``, ``slug``, ``title``, ``draft``, anything else).
    """
    new: dict[str, Any] = {
        "id": new_id,
        "type": new_type,
        "status": legacy.get("status"),
        "priority": "00",
        "slug": legacy.get("slug"),
        "title": legacy.get("title"),
    }
    if "draft" in legacy:
        new["draft"] = legacy["draft"]
    for k, v in legacy.items():
        if k in ("status", "slug", "title", "epic", "feature", "kind", "draft"):
            continue
        new[k] = v
    return new


def _write_tree(
    items: list[_Source],
    id_map: dict[Path, str],
    paths: dict[Path, Path],
    parents: dict[Path, Path | None],
) -> None:
    children = set(p for p in parents.values() if p is not None)
    for item in items:
        new_path = paths[item.source_path]
        new_id = id_map[item.source_path]
        if item.source_path in children:
            new_path.mkdir(parents=True, exist_ok=True)
            body_file = new_path / special_file_name(new_id)
        else:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            body_file = new_path
        new_fm = _rewrite_frontmatter(
            item.frontmatter, new_id, _LEGACY_TYPE_MAP[item.kind]
        )
        body_file.write_text(emit_frontmatter(new_fm, item.body), encoding="utf-8")


def _build_report(
    items: list[_Source],
    id_map: dict[Path, str],
    paths: dict[Path, Path],
    source: Path,
    dest: Path,
) -> dict[str, dict[str, str]]:
    report: dict[str, dict[str, str]] = {}
    for item in sorted(items, key=lambda i: str(i.source_path)):
        rel_src = str(item.source_path.relative_to(source))
        rel_new = str(paths[item.source_path].relative_to(dest))
        report[rel_src] = {"new_path": rel_new, "new_id": id_map[item.source_path]}
    return report


def migrate(
    source: Path = typer.Argument(..., help="Legacy tasks/ directory to read."),
    dest: Path = typer.Argument(..., help="Empty directory to write the new tree to."),
) -> None:
    """Migrate a legacy taskman tree to the new uniform recursive layout."""
    source = source.resolve()
    dest = dest.resolve()
    if not source.is_dir():
        typer.echo(f"Error: source is not a directory: {source}", err=True)
        raise typer.Exit(1)
    if dest.exists() and any(dest.iterdir()):
        typer.echo(f"Error: dest is not empty: {dest}", err=True)
        raise typer.Exit(1)
    dest.mkdir(parents=True, exist_ok=True)
    # Always create the three top-level status folders so downstream tools
    # (validate, list) see a fully-shaped tree even when one folder is empty.
    for status_folder in ("backlog", "active", "done"):
        (dest / status_folder).mkdir(exist_ok=True)

    try:
        items = _load_source(source)
        if not items:
            typer.echo("(no items to migrate)")
            (dest / "_migration_report.json").write_text("{}\n", encoding="utf-8")
            return
        id_map = _allocate_ids(items)
        parents = _build_parent_map(items)
        paths = _compute_paths(items, dest, id_map, parents)
        _write_tree(items, id_map, paths, parents)
        report = _build_report(items, id_map, paths, source, dest)
    except MigrationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    (dest / "_migration_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    typer.echo(f"Migrated {len(items)} item(s) to {dest}")
