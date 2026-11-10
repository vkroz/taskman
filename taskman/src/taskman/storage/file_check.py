"""File backend: layout checks across the work-item tree.

Walks every entry under ``tasks_dir`` and reports per-item findings:

- **bad_name_grammar** — file/directory name does not parse.
- **wrong_location** — item not under a recognized status folder.
- **name_folder_drift** — top-level item's in-name status disagrees with its
  containing folder.
- **orphan_parent** — nested item whose parent directory is not a valid
  work-item name.
- **task_not_leaf** — a ``task`` item exists in directory form.
- **missing_body** — body file (the path itself or ``_<id>.md``) is missing.
- **frontmatter_parse_error** — YAML frontmatter cannot be parsed.
- **name_yaml_drift** — frontmatter field disagrees with the in-name token.
- **missing_field** — frontmatter is missing a field that the name carries.
- **orphan_special_file** — ``_<id>.md`` sits outside a valid work-item dir.
- **special_file_id_mismatch** — ``_<id>.md`` ID does not match its parent.
- **malformed_depends_on** — ``depends_on`` is present but not a list.

Dependency checks that apply to every backend live in ``taskman.integrity``.
"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml.error import YAMLError

from taskman.integrity import Finding
from taskman.model.layout import is_status_folder
from taskman.model.names import (
    NameParseError,
    name_is_special_file,
    parse_name,
    special_file_name,
)
from taskman.model.yaml_io import read_file


def check_layout(tasks_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not tasks_dir.exists():
        findings.append(
            Finding(
                path=str(tasks_dir),
                severity="error",
                code="missing_tasks_dir",
                message=f"tasks directory does not exist: {tasks_dir}",
            )
        )
        return findings

    for path in sorted(tasks_dir.rglob("*"), key=lambda p: str(p)):
        rel = path.relative_to(tasks_dir)
        rel_parts = rel.parts

        # Skip top-level status folders themselves.
        if len(rel_parts) == 1 and path.is_dir() and is_status_folder(path.name):
            continue

        # Skip top-level meta files (any underscore-prefixed file like
        # `_migration_report.json`). Convention: top-level `_*` are meta.
        if len(rel_parts) == 1 and path.is_file() and path.name.startswith("_"):
            continue

        # Special body files: verify location and ID match.
        if name_is_special_file(path.name):
            findings.extend(_check_special_file(path, tasks_dir))
            continue

        # Anything else must sit under a status folder.
        if len(rel_parts) < 2:
            findings.append(
                Finding(
                    str(path),
                    "error",
                    "wrong_location",
                    "item is directly under tasks_dir, not within a status folder",
                )
            )
            continue
        if not is_status_folder(rel_parts[0]):
            findings.append(
                Finding(
                    str(path),
                    "error",
                    "wrong_location",
                    f"top folder {rel_parts[0]!r} is not a status folder",
                )
            )
            continue

        # Try to parse the name.
        try:
            wi = parse_name(path.name)
        except NameParseError as exc:
            findings.append(
                Finding(str(path), "error", "bad_name_grammar", str(exc))
            )
            continue

        # Top-level vs nested.
        is_top_level = len(rel_parts) == 2
        if is_top_level:
            if wi.status != rel_parts[0]:
                findings.append(
                    Finding(
                        str(path),
                        "error",
                        "name_folder_drift",
                        f"name status {wi.status!r} disagrees with folder {rel_parts[0]!r}",
                    )
                )
        else:
            # Nested: parent directory must itself be a parseable work item.
            try:
                parse_name(path.parent.name)
            except NameParseError:
                findings.append(
                    Finding(
                        str(path),
                        "error",
                        "orphan_parent",
                        f"parent {path.parent.name!r} is not a valid work item",
                    )
                )

        # task = leaf invariant: task items must be file form.
        if wi.item_type == "task" and path.is_dir():
            findings.append(
                Finding(
                    str(path),
                    "error",
                    "task_not_leaf",
                    "task item is in directory form (must be a leaf file)",
                )
            )

        # Frontmatter drift checks.
        body_file = (
            path / special_file_name(wi.item_id) if path.is_dir() else path
        )
        if not body_file.exists():
            findings.append(
                Finding(
                    str(path),
                    "error",
                    "missing_body",
                    f"body file missing: {body_file}",
                )
            )
            continue
        try:
            data, _ = read_file(body_file)
        except (ValueError, YAMLError) as exc:
            findings.append(
                Finding(
                    str(path), "error", "frontmatter_parse_error", str(exc)
                )
            )
            continue

        name_values = {
            "id": wi.item_id,
            "type": wi.item_type,
            "status": wi.status,
            "priority": wi.priority,
            "slug": wi.slug,
        }
        for field, name_val in name_values.items():
            yaml_val = data.get(field)
            if yaml_val is None:
                findings.append(
                    Finding(
                        str(path),
                        "warning",
                        "missing_field",
                        f"frontmatter missing {field!r}",
                    )
                )
            elif str(yaml_val) != str(name_val):
                findings.append(
                    Finding(
                        str(path),
                        "error",
                        "name_yaml_drift",
                        f"{field}: name={name_val!r} yaml={yaml_val!r}",
                    )
                )

        deps_raw = data.get("depends_on")
        if deps_raw is not None and not isinstance(deps_raw, list):
            findings.append(
                Finding(
                    str(path),
                    "warning",
                    "malformed_depends_on",
                    f"depends_on must be a list, got: {type(deps_raw).__name__}",
                )
            )

    return findings


def _check_special_file(path: Path, tasks_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    parent = path.parent
    if parent == tasks_dir or (
        parent.parent == tasks_dir and is_status_folder(parent.name)
    ):
        findings.append(
            Finding(
                str(path),
                "error",
                "orphan_special_file",
                "special body file outside a work-item directory",
            )
        )
        return findings
    try:
        parent_wi = parse_name(parent.name)
    except NameParseError:
        findings.append(
            Finding(
                str(path),
                "error",
                "orphan_special_file",
                f"parent {parent.name!r} is not a valid work item",
            )
        )
        return findings
    expected = special_file_name(parent_wi.item_id)
    if path.name != expected:
        findings.append(
            Finding(
                str(path),
                "error",
                "special_file_id_mismatch",
                f"expected {expected}, got {path.name}",
            )
        )
    return findings
