"""Render a task into a file in the git checkout. The server never writes this."""
from __future__ import annotations

from pathlib import Path


def repo_root(start: Path) -> Path:
    """Directory that contains ``.git``, or ``start`` when this is not a checkout."""
    current = start.resolve()
    for directory in (current, *current.parents):
        if (directory / ".git").exists():
            return directory
    return current


def task_filename(task_id) -> str:
    """Numeric id only. A server-provided title or path must not become a filename."""
    text = str(task_id).strip()
    if not text.isdigit():
        raise ValueError(f"task id must be numeric: {task_id!r}")
    return text


def ensure_gitignore(root: Path) -> bool:
    """Append ``.taskman/`` when it is missing. Returns True if the file changed."""
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries = {line.strip() for line in existing.splitlines()}
    if ".taskman/" in entries or ".taskman" in entries:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}.taskman/\n")
    return True


def render_working_copy(payload: dict, server_url: str) -> str:
    task = payload["task"]
    lines = [
        f"# {task['title']}",
        "",
        f"- id: {task_filename(task['id'])}",
        f"- type: {task['type']}",
        f"- status: {task['status']}",
        f"- server: {server_url}",
        "",
        task.get("body") or "",
        "",
        "## Activity",
    ]
    events = payload.get("events") or []
    if not events:
        lines.append("- none yet")
    for event in events:
        who = event.get("agent") or "unknown"
        lines.append(f"- {event.get('created_at')} {event.get('kind')} {who} {event.get('detail') or ''}".rstrip())
    lines.append("")
    return "\n".join(lines)


def write_working_copy(start: Path, payload: dict, server_url: str) -> Path:
    root = repo_root(start)
    task_id = task_filename(payload["task"]["id"])
    directory = root / ".taskman" / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.md"
    path.write_text(render_working_copy(payload, server_url), encoding="utf-8")
    (root / ".taskman" / "active").write_text(task_id + "\n", encoding="utf-8")
    ensure_gitignore(root)
    return path


def active_task_id(start: Path) -> str | None:
    marker = repo_root(start) / ".taskman" / "active"
    if not marker.is_file():
        return None
    text = marker.read_text(encoding="utf-8").strip()
    first = text.splitlines()[0].strip() if text else ""
    if first.isdigit():
        return first
    return None


def clear_active(start: Path) -> None:
    marker = repo_root(start) / ".taskman" / "active"
    marker.unlink(missing_ok=True)


def clear_active_if(start: Path, task_id) -> bool:
    """Remove ``.taskman/active`` when it names ``task_id``. Returns True if removed."""
    if active_task_id(start) != str(task_id):
        return False
    clear_active(start)
    return True
