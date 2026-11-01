"""Report commits, pull requests, and completion from host hooks.

Claude Code runs ``hooks/report.sh``, which executes ``taskman hook claude``.
Codex has no hook entry in this plugin; ``taskman hooks install`` writes a git
post-commit hook that runs ``taskman hook git commit``. Hooks exit 0 when the
server cannot be reached so a commit or agent turn is not blocked.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from taskman.client import ApiError, client_from_env
from taskman.workingcopy import active_task_id, clear_active, ensure_gitignore, repo_root

hook_app = typer.Typer(help="Entry points used by editor and git hooks.")
hooks_app = typer.Typer(help="Install the git post-commit fallback.")

# Shorter than plugins/tm/hooks/report.sh (4s) and the Claude hook timeout (5s).
HOOK_CLIENT_TIMEOUT = 3

_COMMIT = re.compile(
    r"(?:^|&&|\|\||;|\n)\s*git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+)*commit\b",
    re.MULTILINE,
)
_PR_COMMAND = re.compile(r"(?:^|&&|\|\||;|\n)\s*gh\s+pr\s+create\b", re.MULTILINE)
_PR_URL = re.compile(r"https://\S+/pull/\d+")
_COMPLETION = re.compile(r"^Taskman completion:\s*(\d+)\s*$", re.MULTILINE)
_HOOK_BODY = "#!/bin/sh\n# Added by Taskman. Reports the commit for .taskman/active.\ntaskman hook git commit\n"


def _fail_open(exc: Exception) -> None:
    typer.echo(f"Taskman hook could not report: {exc}", err=True)
    raise typer.Exit(0)


def _git_head(cwd: Path) -> tuple[str, str]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=False
    )
    if sha.returncode != 0:
        raise ApiError(sha.stderr.strip() or "git rev-parse failed")
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=cwd, capture_output=True, text=True, check=False
    )
    return sha.stdout.strip(), subject.stdout.strip()


def _client():
    return client_from_env(timeout=HOOK_CLIENT_TIMEOUT)


def _post_event(task_id: str, kind: str, agent: str, session: str, **fields: str) -> None:
    _client().request(
        "POST",
        f"/api/tasks/{task_id}/events",
        {"kind": kind, "agent": agent, "session": session, **fields},
    )


def _response_text(response) -> tuple[str, str, bool]:
    if isinstance(response, str):
        return response, "", False
    if not isinstance(response, dict):
        return "", "", False
    failed = bool(response.get("is_error") or response.get("interrupted"))
    stderr = str(response.get("stderr") or "")
    stdout = str(response.get("stdout") or "")
    blob = f"{stdout}\n{stderr}".lower()
    if "fatal:" in blob or "nothing to commit" in blob:
        failed = True
    return stdout, stderr, failed


def _assistant_text(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        if "content" in value:
            return _assistant_text(value.get("content"))
        text = value.get("text")
        if isinstance(text, str):
            return text
    return None


def _is_assistant(obj: dict) -> bool:
    if obj.get("type") in {"assistant", "assistant_message"}:
        return True
    message = obj.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant":
        return True
    return obj.get("role") == "assistant"


def _last_assistant_message(path: Path) -> str:
    """Text of the last assistant turn. Other transcript text is ignored."""
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")[-100_000:]
    last = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not _is_assistant(obj):
            continue
        message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        text = _assistant_text(message.get("content") if isinstance(message, dict) else None)
        if text is None and isinstance(message, dict):
            text = _assistant_text(message.get("text"))
        if text:
            last = text
    return last


def _completion_marker(path: Path, task_id: str) -> bool:
    text = _last_assistant_message(path)
    return task_id in _COMPLETION.findall(text)


def _handle_claude(payload: dict) -> None:
    event = payload.get("hook_event_name") or payload.get("hook_event") or ""
    cwd = Path(payload.get("cwd") or Path.cwd())
    session = str(payload.get("session_id") or os.environ.get("TASKMAN_SESSION") or "")
    agent = os.environ.get("TASKMAN_AGENT") or "claude-code"
    task_id = active_task_id(cwd)
    if event == "Stop":
        transcript = payload.get("transcript_path") or ""
        if task_id and transcript and _completion_marker(Path(transcript), task_id):
            _client().request(
                "POST",
                f"/api/tasks/{task_id}/complete",
                {"message": "completed", "agent": agent, "session": session},
            )
            clear_active(cwd)
            typer.echo(f"completed {task_id}")
        return
    if event != "PostToolUse":
        return
    if payload.get("tool_name") not in (None, "", "Bash"):
        return
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if "taskman hook" in command:
        return
    stdout, _stderr, failed = _response_text(payload.get("tool_response"))
    if failed or not task_id:
        return
    if _COMMIT.search(command):
        sha, subject = _git_head(repo_root(cwd))
        _post_event(task_id, "commit", agent, session, commit_sha=sha, message=subject)
        typer.echo(f"reported commit {sha[:12]} for {task_id}")
    if _PR_COMMAND.search(command):
        urls = _PR_URL.findall(stdout)
        if not urls:
            return
        _post_event(task_id, "pull_request", agent, session, pr_url=urls[0], message=urls[0])
        typer.echo(f"reported pull request {urls[0]} for {task_id}")


@hook_app.command("claude")
def hook_claude() -> None:
    """Read a Claude Code hook payload on stdin and report commit, PR, or completion."""
    raw = sys.stdin.read()
    if not raw.strip():
        typer.echo("taskman hook claude expects a JSON payload on stdin.", err=True)
        raise typer.Exit(0)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        _fail_open(exc)
        return
    if not isinstance(payload, dict):
        raise typer.Exit(0)
    try:
        _handle_claude(payload)
    except Exception as exc:  # noqa: BLE001 — a hook must not block the editor
        _fail_open(exc)


@hook_app.command("git")
def hook_git(action: str = typer.Argument(..., help="commit")) -> None:
    """Git-hook entry. `commit` reports HEAD for the active task."""
    if action != "commit":
        typer.echo("Only `taskman hook git commit` is supported.", err=True)
        raise typer.Exit(0)
    cwd = Path.cwd()
    task_id = active_task_id(cwd)
    if not task_id:
        typer.echo("No .taskman/active task; commit was not reported.", err=True)
        raise typer.Exit(0)
    try:
        sha, subject = _git_head(repo_root(cwd))
        agent = os.environ.get("TASKMAN_AGENT") or "git"
        session = os.environ.get("TASKMAN_SESSION") or ""
        _post_event(task_id, "commit", agent, session, commit_sha=sha, message=subject)
        typer.echo(f"reported commit {sha[:12]} for {task_id}")
    except Exception as exc:  # noqa: BLE001 — a git hook must not reject the commit
        _fail_open(exc)


@hooks_app.command("install")
def hooks_install(force: bool = typer.Option(False, "--force", help="Replace an existing post-commit hook.")) -> None:
    """Install a git post-commit hook. This is the Codex fallback and works for any git repo."""
    probe = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        typer.echo(probe.stderr.strip() or "Not a git repository.", err=True)
        raise typer.Exit(2)
    hooks_dir = Path(probe.stdout.strip())
    if not hooks_dir.is_absolute():
        hooks_dir = Path.cwd() / hooks_dir
    hooks_dir.mkdir(parents=True, exist_ok=True)
    destination = hooks_dir / "post-commit"
    if destination.exists():
        current = destination.read_text(encoding="utf-8", errors="replace")
        if "taskman hook git commit" in current and not force:
            typer.echo(f"Taskman post-commit hook already present at {destination}")
            _note_gitignore()
            return
        if not force:
            typer.echo(
                f"{destination} already exists. Merge `taskman hook git commit` into it, or re-run with --force.",
                err=True,
            )
            raise typer.Exit(2)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = destination.with_name(f"post-commit.bak.{stamp}")
        shutil.copy2(destination, backup)
        typer.echo(f"Backed up the existing hook to {backup}")
    destination.write_text(_HOOK_BODY, encoding="utf-8")
    destination.chmod(0o755)
    typer.echo(f"Installed {destination}")
    _note_gitignore()
    typer.echo(
        "Claude Code loads plugin hooks for commits, pull requests, and completion. "
        "Codex uses this git hook for commits. After `gh pr create`, run "
        "`taskman report pr --url <pull-request-url>` when no plugin hook is active."
    )


def _note_gitignore() -> None:
    root = repo_root(Path.cwd())
    if ensure_gitignore(root):
        typer.echo(f"Added .taskman/ to {root / '.gitignore'}. The working copy stays out of git.")
    else:
        typer.echo("Keep .taskman/ out of git. It belongs in .gitignore.")
