"""File backend: work items as Markdown files under ``tasks/``."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml.error import YAMLError

from taskman.integrity import Finding
from taskman.model.ids import allocate_id, today_prefix
from taskman.model.layout import find_item_by_id, is_top_level_item, iter_work_items, parent_item_path
from taskman.model.locking import mutation_lock
from taskman.model.names import NameParseError, WorkItemName, emit_name, parse_name, special_file_name
from taskman.model.ordering import read_order, write_order
from taskman.model.yaml_io import read_file, write_file
from taskman.storage.file_check import check_layout
from taskman.storage.store import Item, StorageError


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


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _merge(data: Any, metadata: dict[str, Any]) -> None:
    """Write ``metadata`` into loaded frontmatter, keeping untouched values as loaded.

    Unchanged values keep their YAML formatting. A list that only grew (a
    journal append) keeps its existing entries and gains the new ones.
    """
    for key in [key for key in data if key not in metadata]:
        del data[key]
    for key, value in metadata.items():
        if key not in data:
            data[key] = value
            continue
        current = to_plain(data[key])
        if current == value:
            continue
        if isinstance(current, list) and isinstance(value, list) and value[: len(current)] == current:
            data[key].extend(value[len(current) :])
        else:
            data[key] = value


def _parent_id(parent: Path | None) -> str | None:
    """The parent's ID; ``None`` for a top-level item or a parent whose name does not parse."""
    if parent is None:
        return None
    try:
        return parse_name(parent.name).item_id
    except NameParseError:
        return None


class FileStore:
    consolidation = True
    reorder_done = True

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

    def lock(self):
        return mutation_lock(self.tasks_dir)

    def create(
        self,
        *,
        item_type: str,
        title: str,
        body: str,
        parent_id: str | None,
        slug: str,
        status: str,
        draft: bool,
        project: str | None = None,
    ) -> Item:
        item_id = allocate_id(self.tasks_dir)
        slug = self._unique_slug(slug, today_prefix())
        if parent_id is None:
            parent_dir = self.tasks_dir / status
            parent_dir.mkdir(parents=True, exist_ok=True)
        else:
            parent_dir = self._directory_form(self._path(parent_id), parent_id)
        name = WorkItemName(priority="00", item_id=item_id, item_type=item_type, status=status, slug=slug)  # type: ignore[arg-type]
        path = parent_dir / emit_name(name, as_directory=False)
        if path.exists():
            # Defensive — ID uniqueness should prevent this.
            raise StorageError(f"target already exists: {path}")
        data = {
            "id": item_id,
            "type": item_type,
            "status": status,
            "priority": "00",
            "slug": slug,
            "title": title,
            "draft": draft,
        }
        write_file(path, data, body)
        return self._item(path)

    def save(self, item_id: str, metadata: dict[str, Any], body: str) -> Item:
        path = self._path(item_id)
        body_file = self._body_file(path, item_id)
        data, _ = read_file(body_file)
        _merge(data, metadata)
        write_file(body_file, data, body)
        return self._item(path)

    def set_status(self, item_id: str, status: str) -> Item:
        path = self._path(item_id)
        top_level = is_top_level_item(path, self.tasks_dir)
        target_dir = self.tasks_dir / status if top_level else path.parent
        new_path = self._rename(path, target_dir, status=status)
        self._set_field(new_path, item_id, "status", status)
        return self._item(new_path)

    def set_type(self, item_id: str, item_type: str) -> Item:
        path = self._path(item_id)
        if item_type == "task" and path.is_dir():
            raise StorageError(
                f"cannot convert {item_id} to task — item is in directory form. "
                f"Remove the directory representation first."
            )
        new_path = self._rename(path, path.parent, item_type=item_type)
        self._set_field(new_path, item_id, "type", item_type)
        return self._item(new_path)

    def delete(self, item_id: str) -> None:
        path = self._path(item_id)
        if path.is_dir():
            # A directory item whose children are gone holds only its body file.
            self._body_file(path, item_id).unlink()
            path.rmdir()
        else:
            path.unlink()

    def save_order(self, parent_key: str, item_ids: list[str], moved_id: str) -> None:
        parents = read_order(self.tasks_dir)
        parents[parent_key] = item_ids
        write_order(self.tasks_dir, parents)

    def check_layout(self) -> list[Finding]:
        return check_layout(self.tasks_dir)

    def history_link(self, item_id: str) -> str:
        original = self._body_file(self._path(item_id), item_id)
        return os.path.relpath(original, history_dir(self.tasks_dir))

    def save_history(self, record: dict[str, Any]) -> dict[str, Any]:
        directory = history_dir(self.tasks_dir)
        stored = {**record, "path": str(Path("docs") / "taskman-history" / f"{record['key']}.md")}
        atomic_text(directory / f"{record['key']}.md", record["summary"])
        atomic_text(directory / f"{record['key']}.json", json.dumps(stored, indent=2))
        return stored

    def _path(self, item_id: str) -> Path:
        path = find_item_by_id(self.tasks_dir, item_id)
        if path is None:
            raise StorageError(f"item not found: {item_id}")
        return path

    def _unique_slug(self, slug: str, today: str) -> str:
        """Append ``_2``, ``_3``, … if ``slug`` is already in use today."""
        in_use = {
            name.slug
            for name in (parse_name(path.name) for path in iter_work_items(self.tasks_dir))
            if name.item_id.startswith(today)
        }
        if slug not in in_use:
            return slug
        n = 2
        while f"{slug}_{n}" in in_use:
            n += 1
        return f"{slug}_{n}"

    @staticmethod
    def _directory_form(path: Path, item_id: str) -> Path:
        """Return the item's directory, converting a leaf file into a directory first.

        The original body moves into ``_<id>.md`` inside the new directory.
        """
        if path.is_dir():
            return path
        if not path.name.endswith(".md"):
            raise StorageError(f"file parent must end with .md: {path.name}")
        dir_path = path.parent / path.name[:-3]
        if dir_path.exists():
            raise StorageError(f"cannot convert: directory already exists: {dir_path}")
        content = path.read_text(encoding="utf-8")
        dir_path.mkdir()
        (dir_path / special_file_name(item_id)).write_text(content, encoding="utf-8")
        path.unlink()
        return dir_path

    @staticmethod
    def _rename(path: Path, target_dir: Path, **changes: str) -> Path:
        name = parse_name(path.name)
        new_name = WorkItemName(
            priority=name.priority,
            item_id=name.item_id,
            item_type=changes.get("item_type", name.item_type),  # type: ignore[arg-type]
            status=changes.get("status", name.status),  # type: ignore[arg-type]
            slug=name.slug,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        new_path = target_dir / emit_name(new_name, as_directory=path.is_dir())
        if new_path.exists() and new_path != path:
            raise StorageError(f"target path already exists: {new_path}")
        # Atomic rename (single syscall on POSIX) moves the file or whole dir tree.
        path.rename(new_path)
        return new_path

    def _set_field(self, path: Path, item_id: str, key: str, value: str) -> None:
        body_file = self._body_file(path, item_id)
        data, body = read_file(body_file)
        data[key] = value
        write_file(body_file, data, body)

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
            parent_id=_parent_id(parent),
            metadata=metadata,
            body=body,
            location=str(path),
            error=error,
            modified=body_file.stat().st_mtime if body_file.exists() else 0.0,
        )
