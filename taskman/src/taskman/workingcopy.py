"""Render a task into a file in the git checkout. The server never writes this."""
from __future__ import annotations

from pathlib import Path


def render_working_copy(payload: dict, server_url: str) -> str:
    task = payload["task"]
    lines = [
        f"# {task['title']}",
        "",
        f"- id: {task['id']}",
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


def write_working_copy(root: Path, payload: dict, server_url: str) -> Path:
    task_id = str(payload["task"]["id"])
    directory = root / ".taskman" / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.md"
    path.write_text(render_working_copy(payload, server_url), encoding="utf-8")
    (root / ".taskman" / "active").write_text(task_id + "\n", encoding="utf-8")
    return path


def active_task_id(start: Path) -> str | None:
    for directory in (start, *start.parents):
        marker = directory / ".taskman" / "active"
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            first = text.splitlines()[0].strip() if text else ""
            if first.isdigit():
                return first
        if (directory / ".git").exists():
            break
    return None
