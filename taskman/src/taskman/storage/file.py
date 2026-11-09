"""File backend: work items as Markdown files under ``tasks/``."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ruamel.yaml.error import YAMLError

from taskman.model.layout import find_item_by_id, iter_work_items, parent_item_path
from taskman.model.names import parse_name, special_file_name
from taskman.model.ordering import read_order
from taskman.model.yaml_io import read_file
from taskman.storage import Item


def get_tasks_dir() -> Path:
    """Resolve the active tasks directory.

    Reads ``TASKMAN_TASKS_DIR`` from the environment if set; otherwise
    uses the nearest ``tasks/`` directory, stopping at the repository root.
    """
    env = os.environ.get("TASKMAN_TASKS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    for directory in (Path.cwd(), *Path.cwd().parents):
        if (directory / "tasks").is_dir():
            return directory / "tasks"
        if (directory / ".git").exists():
            return directory / "tasks"
    return Path.cwd() / "tasks"


def history_dir(tasks_dir: Path) -> Path:
    return tasks_dir.parent / "docs" / "taskman-history"


def to_plain(obj: Any) -> Any:
    """Recursively convert ruamel.yaml containers to plain dict/list/scalar."""
    if hasattr(obj, "items") and callable(obj.items):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_plain(v) for v in obj]
    return obj


class FileStore:
    def __init__(self, tasks_dir: Path) -> None:
        self.tasks_dir = tasks_dir

    def items(self) -> list[Item]:
        return [self._item(path) for path in iter_work_items(self.tasks_dir)]

    def get(self, item_id: str) -> Item | None:
        path = find_item_by_id(self.tasks_dir, item_id)
        return self._item(path) if path else None

    def document(self, item_id: str) -> str:
        path = find_item_by_id(self.tasks_dir, item_id)
        if path is None:
            raise KeyError(item_id)
        return self._body_file(path, item_id).read_text(encoding="utf-8")

    def histories(self) -> list[dict]:
        directory = history_dir(self.tasks_dir)
        records = []
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text())
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("ids"), list)
                    or not all(isinstance(i, str) for i in value["ids"])
                ):
                    continue
                if not all(
                    isinstance(value.get(k), str)
                    for k in ("key", "title", "summary", "path")
                ):
                    continue
                # Never hide records if the published documentation has disappeared.
                if not (directory / (value["key"] + ".md")).is_file():
                    continue
                records.append(value)
            except (OSError, ValueError, TypeError):
                continue
        return records

    def sibling_order(self) -> dict[str, list[str]]:
        return read_order(self.tasks_dir)

    @staticmethod
    def _body_file(path: Path, item_id: str) -> Path:
        return path / special_file_name(item_id) if path.is_dir() else path

    def _item(self, path: Path) -> Item:
        name = parse_name(path.name)
        parent = parent_item_path(path, self.tasks_dir)
        body_file = self._body_file(path, name.item_id)
        metadata: dict[str, Any] = {}
        body = ""
        error = None
        if not body_file.exists():
            error = f"body file missing: {body_file}"
        else:
            try:
                data, body = read_file(body_file)
                metadata = to_plain(data)
            except (ValueError, OSError, YAMLError) as exc:
                # Missing fences, invalid YAML, or undecodable text.
                error = str(exc)
        return Item(
            id=name.item_id,
            type=name.item_type,
            status=name.status,
            priority=name.priority,
            slug=name.slug,
            parent_id=parse_name(parent.name).item_id if parent else None,
            metadata=metadata,
            body=body,
            location=str(path),
            error=error,
        )
