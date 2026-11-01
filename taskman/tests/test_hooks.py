"""Commit, pull request, and completion reporting."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.httpapp import make_application

runner = CliRunner()
TOKEN = "test-token-value"


class _Quiet(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return


@pytest.fixture
def server(database):
    httpd = make_server("127.0.0.1", 0, make_application(TOKEN), handler_class=_Quiet)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def _env(base: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"TASKMAN_URL": base, "TASKMAN_ACCESS_TOKEN": TOKEN, "TASKMAN_AGENT": "claude-code"})
    return env


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("hello\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "first cut")
    return repo


def _task(server: str, repo: Path) -> str:
    created = runner.invoke(
        app,
        ["new", "--title", "Hooked work", "--json"],
        env=_env(server),
    )
    assert created.exit_code == 0, created.output
    task_id = str(json.loads(created.output)["id"])
    marker = repo / ".taskman"
    marker.mkdir()
    (marker / "active").write_text(task_id + "\n")
    return task_id


def test_commit_pr_and_completion_hooks(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    commit = runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "session_id": "sess-9",
                "cwd": str(repo),
                "tool_input": {"command": "git commit -m 'first cut'"},
                "tool_response": {"stdout": "", "stderr": ""},
            }
        ),
        env=_env(server),
    )
    assert commit.exit_code == 0, commit.output
    pr = runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "session_id": "sess-9",
                "cwd": str(repo),
                "tool_input": {"command": "gh pr create --title work"},
                "tool_response": {"stdout": "https://github.com/acme/widgets/pull/12\n"},
            }
        ),
        env=_env(server),
    )
    assert pr.exit_code == 0, pr.output
    transcript = tmp_path / "transcript.jsonl"
    earlier = json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": f"Taskman completion: {task_id}9"}]},
    })
    latest = json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": f"Checked it.\nTaskman completion: {task_id}\n"}],
        },
    })
    transcript.write_text(earlier + "\n" + latest + "\n")
    done = runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-9",
                "cwd": str(repo),
                "transcript_path": str(transcript),
            }
        ),
        env=_env(server),
    )
    assert done.exit_code == 0, done.output
    assert not (repo / ".taskman" / "active").exists()
    board = json.loads(runner.invoke(app, ["activity", "--json"], env=_env(server)).output)
    row = next(item for item in board["rows"] if str(item["id"]) == task_id)
    assert row["status"] == "done"
    assert row["last_event"] == "completion"
    assert row["agent"] == "claude-code"
    assert row["session"] == "sess-9"
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    kinds = [event["kind"] for event in shown["events"]]
    assert kinds == ["commit", "pull_request", "completion"]
    assert shown["events"][0]["commit_sha"] == sha
    assert shown["events"][1]["pr_url"] == "https://github.com/acme/widgets/pull/12"
    # The same Stop payload does not insert a second completion.
    runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-9",
                "cwd": str(repo),
                "transcript_path": str(transcript),
            }
        ),
        env=_env(server),
    )
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert [event["kind"] for event in shown["events"]].count("completion") == 1


def test_hook_without_an_active_task_does_not_fail(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    result = runner.invoke(
        app,
        ["hook", "git", "commit"],
        env=_env(server),
    )
    assert result.exit_code == 0
    assert "not reported" in (result.stderr or result.output)


def test_hook_fails_open_when_the_server_is_down(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / ".taskman").mkdir()
    (repo / ".taskman" / "active").write_text("1\n")
    result = runner.invoke(
        app,
        ["hook", "git", "commit"],
        env={**os.environ, "TASKMAN_URL": "http://127.0.0.1:1", "TASKMAN_ACCESS_TOKEN": TOKEN},
    )
    assert result.exit_code == 0
    assert "could not report" in (result.stderr or result.output)


def test_git_hook_install_writes_post_commit_and_refuses_to_clobber(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    installed = runner.invoke(app, ["hooks", "install"])
    assert installed.exit_code == 0, installed.output
    hook = repo / ".git" / "hooks" / "post-commit"
    assert "taskman hook git commit" in hook.read_text()
    assert hook.stat().st_mode & 0o111
    hook.write_text("#!/bin/sh\necho custom\n")
    refused = runner.invoke(app, ["hooks", "install"])
    assert refused.exit_code == 2
    assert "custom" in hook.read_text()
    forced = runner.invoke(app, ["hooks", "install", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "taskman hook git commit" in hook.read_text()
    backups = list((repo / ".git" / "hooks").glob("post-commit.bak.*"))
    assert backups
    assert "custom" in backups[0].read_text()
    assert ".taskman/" in (repo / ".gitignore").read_text()


def _assistant(text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


def test_completion_requires_an_exact_line_in_the_last_assistant_message(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        _assistant(f"Taskman completion: {task_id}")
        + "\n"
        + _assistant(f"Taskman completion: {task_id}2")
        + "\n"
    )
    result = runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps({
            "hook_event_name": "Stop",
            "session_id": "sess-9",
            "cwd": str(repo),
            "transcript_path": str(transcript),
        }),
        env=_env(server),
    )
    assert result.exit_code == 0, result.output
    shown = json.loads(runner.invoke(app, ["show", task_id, "--json"], env=_env(server)).output)
    assert shown["status"] == "backlog"
    assert (repo / ".taskman" / "active").is_file()


def test_pull_request_url_without_gh_pr_create_is_ignored(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    result = runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "session_id": "sess-9",
            "cwd": str(repo),
            "tool_input": {"command": "echo https://github.com/acme/widgets/pull/12"},
            "tool_response": {"stdout": "https://github.com/acme/widgets/pull/12\n"},
        }),
        env=_env(server),
    )
    assert result.exit_code == 0, result.output
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert shown["events"] == []


def test_commit_variants_report_once_and_a_failed_commit_does_not(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    command = f"echo skip\ngit -C {repo} -c user.email=a@b.co commit -m work"
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "sess-9",
        "cwd": str(repo / "missing"),
        "tool_input": {"command": command},
        "tool_response": {"stdout": "", "stderr": ""},
    }
    (repo / "missing").mkdir()
    first = runner.invoke(app, ["hook", "claude"], input=json.dumps(payload), env=_env(server))
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["hook", "claude"], input=json.dumps(payload), env=_env(server))
    assert second.exit_code == 0, second.output
    failed = runner.invoke(
        app,
        ["hook", "claude"],
        input=json.dumps({
            **payload,
            "tool_response": {"stdout": "", "stderr": "nothing to commit, working tree clean"},
        }),
        env=_env(server),
    )
    assert failed.exit_code == 0, failed.output
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    commits = [event for event in shown["events"] if event["kind"] == "commit"]
    assert len(commits) == 1


def test_hook_client_timeout_is_shorter_than_the_shell_timeout(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    (repo / ".taskman").mkdir()
    (repo / ".taskman" / "active").write_text("1\n")
    seen = {}

    def request(self, *args, **kwargs):
        seen["timeout"] = self.timeout
        from taskman.client import ApiError
        raise ApiError("down")

    monkeypatch.setattr("taskman.client.Client.request", request)
    result = runner.invoke(app, ["hook", "git", "commit"], env=_env(server))
    assert result.exit_code == 0
    assert seen["timeout"] <= 4
