"""`worktree-status` command — aggregate active items across git worktrees."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer

from taskman.model.layout import iter_work_items
from taskman.model.names import NameParseError, parse_name, special_file_name
from taskman.model.yaml_io import FrontmatterError, read_file


@dataclass(frozen=True, slots=True)
class _Worktree:
    path: str
    branch: str | None  # None == detached HEAD


def parse_porcelain(text: str) -> list[_Worktree]:
    """Parse the output of ``git worktree list --porcelain``.

    Format (per git docs): blocks separated by blank lines, each block has
    ``worktree <path>``, optional ``branch refs/heads/<name>`` or
    ``detached``. We extract path + branch (or None for detached).
    """
    worktrees: list[_Worktree] = []
    current_path: str | None = None
    current_branch: str | None = None

    def flush() -> None:
        nonlocal current_path, current_branch
        if current_path is not None:
            worktrees.append(_Worktree(path=current_path, branch=current_branch))
        current_path = None
        current_branch = None

    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line:
            flush()
            continue
        if line.startswith("worktree "):
            flush()
            current_path = line[len("worktree ") :]
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            current_branch = (
                ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
            )
    flush()
    return worktrees


def _get_worktrees() -> list[_Worktree]:
    """Run ``git worktree list --porcelain``. Returns []  if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return parse_porcelain(result.stdout)


def _tasks_dir_for(wt: _Worktree) -> Path:
    """Resolve the tasks dir for this worktree.

    Honors the current process's ``TASKMAN_TASKS_DIR`` only if that path
    sits inside this worktree. Otherwise falls back to ``<wt>/tasks/``.
    """
    env = os.environ.get("TASKMAN_TASKS_DIR")
    if env:
        env_path = Path(env).resolve()
        wt_path = Path(wt.path).resolve()
        try:
            env_path.relative_to(wt_path)
            return env_path
        except ValueError:
            pass
    return Path(wt.path) / "tasks"


def _active_items(tasks_dir: Path) -> list[dict[str, str]]:
    """Return items whose in-name status is ``active``."""
    out: list[dict[str, str]] = []
    if not tasks_dir.exists():
        return out
    for path in iter_work_items(tasks_dir):
        try:
            wi = parse_name(path.name)
        except NameParseError:
            continue
        if wi.status != "active":
            continue
        body_file = (
            path / special_file_name(wi.item_id) if path.is_dir() else path
        )
        title = ""
        if body_file.exists():
            try:
                data, _ = read_file(body_file)
                title = str(data.get("title", ""))
            except FrontmatterError:
                pass
        out.append(
            {
                "id": wi.item_id,
                "type": wi.item_type,
                "slug": wi.slug,
                "title": title,
                "path": str(path),
            }
        )
    out.sort(key=lambda i: i["id"])
    return out


def worktree_status(
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Aggregate active work items across all git worktrees."""
    worktrees = _get_worktrees()
    if not worktrees:
        typer.echo(
            "Error: git unavailable or cwd is not a git repository", err=True
        )
        raise typer.Exit(1)

    report: list[dict[str, object]] = []
    for wt in worktrees:
        tasks_dir = _tasks_dir_for(wt)
        if not tasks_dir.exists():
            report.append(
                {
                    "worktree": wt.path,
                    "branch": wt.branch,
                    "items": None,  # signals "(no tasks dir)"
                }
            )
            continue
        report.append(
            {
                "worktree": wt.path,
                "branch": wt.branch,
                "items": _active_items(tasks_dir),
            }
        )

    if as_json:
        typer.echo(json.dumps(report, indent=2))
        return

    for entry in report:
        branch = entry["branch"] or "(detached)"
        typer.echo(f"\n[{entry['worktree']}] @ {branch}")
        items = entry["items"]
        if items is None:
            typer.echo("  (no tasks dir)")
            continue
        if not items:
            typer.echo("  (none)")
            continue
        for item in items:
            typer.echo(f"  {item['id']} {item['type']:5} {item['slug']:30} {item['title']}")
