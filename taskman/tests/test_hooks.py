"""Commit, pull request, and completion reporting."""
from __future__ import annotations

import json
import os
import shutil
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
    env.update({"TASKMAN_URL": base, "TASKMAN_ACCESS_TOKEN": TOKEN})
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
        ["hook", "claude", "--agent", "claude-code"],
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
        ["hook", "claude", "--agent", "claude-code"],
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
        ["hook", "claude", "--agent", "claude-code"],
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
        ["hook", "claude", "--agent", "claude-code"],
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
        ["hook", "git", "commit", "--agent", "Test"],
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
        ["hook", "git", "commit", "--agent", "Test"],
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
        ["hook", "claude", "--agent", "claude-code"],
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
        ["hook", "claude", "--agent", "claude-code"],
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
    first = runner.invoke(app, ["hook", "claude", "--agent", "claude-code"], input=json.dumps(payload), env=_env(server))
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["hook", "claude", "--agent", "claude-code"], input=json.dumps(payload), env=_env(server))
    assert second.exit_code == 0, second.output
    failed = runner.invoke(
        app,
        ["hook", "claude", "--agent", "claude-code"],
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
    result = runner.invoke(app, ["hook", "git", "commit", "--agent", "Test"], env=_env(server))
    assert result.exit_code == 0
    assert seen["timeout"] <= 4


def _stop(server: str, repo: Path, transcript: Path, session: str = "sess-9"):
    return runner.invoke(
        app,
        ["hook", "claude", "--agent", "claude-code"],
        input=json.dumps({
            "hook_event_name": "Stop",
            "session_id": session,
            "cwd": str(repo),
            "transcript_path": str(transcript),
        }),
        env=_env(server),
    )


def test_cli_complete_clears_active_and_the_stop_hook_adds_no_second_completion(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    env = {**_env(server), "TASKMAN_SESSION": ""}
    finished = runner.invoke(app, ["complete", task_id, "--message", "shipped", "--agent", "laptop"], env=env)
    assert finished.exit_code == 0, finished.output
    assert not (repo / ".taskman" / "active").exists()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(_assistant(f"Taskman completion: {task_id}") + "\n")
    assert _stop(server, repo, transcript).exit_code == 0
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert [event["kind"] for event in shown["events"]] == ["completion"]


def test_stop_hook_skips_a_task_that_is_already_done(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    # Completed elsewhere: the server says done but this checkout still names the task.
    moved = runner.invoke(app, ["move", task_id, "done"], env=_env(server))
    assert moved.exit_code == 0, moved.output
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(_assistant(f"Taskman completion: {task_id}") + "\n")
    result = _stop(server, repo, transcript)
    assert result.exit_code == 0, result.output
    assert "already done" in result.output
    assert not (repo / ".taskman" / "active").exists()
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert shown["events"] == []


def test_complete_leaves_an_active_marker_for_another_task(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    other = json.loads(runner.invoke(app, ["new", "--title", "Other", "--json"], env=_env(server)).output)["id"]
    finished = runner.invoke(app, ["complete", str(other), "--agent", "codex"], env=_env(server))
    assert finished.exit_code == 0, finished.output
    assert (repo / ".taskman" / "active").read_text().strip() == task_id


AGENT_REQUIRED = "--agent <author> is required: name who authored these changes"


def test_hooks_trim_the_author(server, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    result = runner.invoke(app, ["hook", "git", "commit", "--agent", "  Ada Lovelace  "], env=_env(server))
    assert result.exit_code == 0, result.output
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert [(event["kind"], event["agent"]) for event in shown["events"]] == [("commit", "Ada Lovelace")]


@pytest.mark.parametrize("command", [["hook", "git", "commit"], ["hook", "claude"]])
@pytest.mark.parametrize("blank", [None, "   "])
def test_hooks_require_an_explicit_author(server, tmp_path, monkeypatch, command, blank):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    args = command if blank is None else [*command, "--agent", blank]
    payload = json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "cwd": str(repo),
        "tool_input": {"command": "git commit -m 'first cut'"},
        "tool_response": {"stdout": ""},
    })
    result = runner.invoke(app, args, input=payload, env={**_env(server), "CLAUDECODE": "1"})
    assert result.exit_code == 1
    assert AGENT_REQUIRED in (result.output + (result.stderr or ""))
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert shown["events"] == []


def test_installed_post_commit_hook_records_the_commit_author(server, tmp_path, monkeypatch):
    if shutil.which("taskman") is None:
        pytest.skip("the taskman console script must be on PATH for a real git hook")
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    task_id = _task(server, repo)
    installed = runner.invoke(app, ["hooks", "install"])
    assert installed.exit_code == 0, installed.output
    assert 'taskman hook git commit --agent "$author"' in (repo / ".git" / "hooks" / "post-commit").read_text()
    subprocess.run(
        ["git", "-c", "user.name=Ada Author", "-c", "user.email=ada@example.com", "commit", "--allow-empty", "-m", "hooked"],
        cwd=repo,
        env=_env(server),
        check=True,
        capture_output=True,
        text=True,
    )
    shown = json.loads(runner.invoke(app, ["context", task_id], env=_env(server)).output)
    assert [(event["kind"], event["agent"], event["message"]) for event in shown["events"]] == [
        ("commit", "Ada Author", "hooked")
    ]


def _run_hook_body(repo: Path, tmp_path: Path, extra_env: dict[str, str]) -> str:
    """Run the installed hook body with a stub taskman. Returns the stub's arguments."""
    from taskman.commands.hook_cmd import _HOOK_BODY

    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(parents=True)
    log = tmp_path / "args"
    stub = stub_dir / "taskman"
    stub.write_text('#!/bin/sh\nprintf "%s|" "$@" > "' + str(log) + '"\n')
    stub.chmod(0o755)
    hook = tmp_path / "post-commit"
    hook.write_text(_HOOK_BODY)
    hook.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        **extra_env,
    }
    subprocess.run([str(hook)], cwd=repo, env=env, check=True)
    return log.read_text()


def test_hook_body_falls_back_to_git_config_user_name_then_git(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init")
    # No commit yet, so `git log -1` fails and user.name is used.
    _git(empty, "config", "user.name", "Config Name")
    assert _run_hook_body(empty, tmp_path / "a", {}) == "hook|git|commit|--agent|Config Name|"
    _git(empty, "config", "--unset", "user.name")
    assert _run_hook_body(empty, tmp_path / "b", {}) == "hook|git|commit|--agent|git|"


def test_hooks_install_flags_an_older_hook_without_an_author(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n# Added by Taskman. Reports the commit for .taskman/active.\ntaskman hook git commit\n")
    refused = runner.invoke(app, ["hooks", "install"])
    assert refused.exit_code == 2
    assert "older Taskman hook" in (refused.output + (refused.stderr or ""))
    forced = runner.invoke(app, ["hooks", "install", "--force"])
    assert forced.exit_code == 0, forced.output
    assert 'taskman hook git commit --agent "$author"' in hook.read_text()
