"""Config trust boundary, redirects, hook script, and working-copy paths."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taskman.cli import app
from taskman.client import ApiError, Client
from taskman.config import resolve_settings, save_server_url
from taskman.workingcopy import write_working_copy

runner = CliRunner()
SCRIPT = Path(__file__).resolve().parents[2] / "plugins" / "tm" / "hooks" / "report.sh"


def test_repo_config_cannot_replace_the_user_url_or_token(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".taskman").mkdir()
    (repo / ".taskman" / "config.json").write_text(
        json.dumps({"url": "http://evil.example", "token": "stolen"})
    )
    user = home / ".config" / "taskman"
    user.mkdir(parents=True)
    (user / "config.json").write_text(json.dumps({"url": "http://127.0.0.1:8080", "token": "user-secret"}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TASKMAN_URL", raising=False)
    monkeypatch.delenv("TASKMAN_ACCESS_TOKEN", raising=False)
    monkeypatch.chdir(repo)
    settings = resolve_settings()
    assert settings.url == "http://127.0.0.1:8080"
    assert settings.token == "user-secret"
    monkeypatch.setenv("TASKMAN_URL", "http://env.example")
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", "env-token")
    settings = resolve_settings()
    assert settings.url == "http://env.example"
    assert settings.token == "env-token"


def test_user_config_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = save_server_url("http://127.0.0.1:8080")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_redirect_to_another_host_is_not_followed(tmp_path):
    seen = []

    class Capture(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    capture = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    capture_thread = threading.Thread(target=capture.serve_forever, daemon=True)
    capture_thread.start()
    target = f"http://127.0.0.1:{capture.server_address[1]}/stolen"

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, format, *args):
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
    redirect_thread.start()
    try:
        client = Client(f"http://127.0.0.1:{redirect.server_address[1]}", "secret-token", timeout=3)
        with pytest.raises(ApiError) as exc:
            client.request("GET", "/api/tasks")
        assert "another host" in str(exc.value)
        assert seen == []
    finally:
        redirect.shutdown()
        capture.shutdown()
        redirect.server_close()
        capture.server_close()


def test_working_copy_stays_at_the_repo_root_and_rejects_a_path_id(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "src"
    (repo / ".git").mkdir(parents=True)
    nested.mkdir()
    payload = {
        "task": {"id": 7, "title": "Work", "type": "task", "status": "active", "body": "Body"},
        "events": [],
    }
    path = write_working_copy(nested, payload, "http://127.0.0.1:8080")
    assert path == repo / ".taskman" / "tasks" / "7.md"
    assert (repo / ".taskman" / "active").read_text().strip() == "7"
    assert ".taskman/" in (repo / ".gitignore").read_text()
    payload["task"]["id"] = "../../etc/passwd"
    with pytest.raises(ValueError):
        write_working_copy(nested, payload, "http://127.0.0.1:8080")
    assert not (tmp_path / "passwd").exists()


def test_list_encodes_the_status_query(monkeypatch):
    seen = {}

    class Fake:
        def request(self, method, path, payload=None, **kwargs):
            seen["path"] = path
            return {"tasks": []}

    monkeypatch.setattr("taskman.commands.work.client_from_env", lambda **kwargs: Fake())
    result = runner.invoke(app, ["list", "--status", "a b"])
    assert result.exit_code == 0, result.output
    assert seen["path"].startswith("/api/tasks?status=")
    assert " " not in seen["path"]
    assert "a" in seen["path"] and "b" in seen["path"]


def _path_with_timeout(bin_dir: Path) -> str:
    timeout = shutil.which("timeout")
    if timeout is None:
        pytest.skip("timeout is required to exercise the hook script")
    return str(bin_dir) + os.pathsep + str(Path(timeout).parent)


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


def test_report_script_exits_zero_when_the_binary_lacks_hook(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    _stub(
        bin_dir,
        "taskman",
        "#!/bin/sh\n"
        "echo \"$*\" >> \"%s\"\n"
        "exit 2\n" % log,
    )
    env = os.environ.copy()
    env["PATH"] = _path_with_timeout(bin_dir)
    env["HOME"] = str(tmp_path / "empty-home")
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert "does not support 'hook claude --agent'" in result.stderr
    assert log.read_text().strip() == "hook claude --agent claude-code"


def test_report_script_exits_zero_when_hook_claude_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    _stub(
        bin_dir,
        "taskman",
        "#!/bin/sh\n"
        "echo called >> \"%s\"\n"
        "exit 1\n" % log,
    )
    env = os.environ.copy()
    env["PATH"] = _path_with_timeout(bin_dir)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        input="{\"hook_event_name\":\"Stop\"}",
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert "called" in log.read_text()


def test_report_script_times_out_a_hung_binary(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(
        bin_dir,
        "taskman",
        "#!/bin/sh\n"
        "sleep 30\n"
        "exit 2\n",
    )
    env = os.environ.copy()
    env["PATH"] = _path_with_timeout(bin_dir)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0


def _bare_path(bin_dir: Path) -> str:
    """PATH with only ``cat`` and the stubs: no timeout and no gtimeout."""
    cat = shutil.which("cat")
    assert cat, "cat is required to exercise the hook script"
    (bin_dir / "cat").symlink_to(cat)
    return str(bin_dir)


def test_report_script_runs_the_hook_without_timeout_or_gtimeout(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    _stub(bin_dir, "taskman", "#!/bin/sh\necho \"$*\" >> \"%s\"\nexit 0\n" % log)
    env = {"PATH": _bare_path(bin_dir), "HOME": str(tmp_path / "empty-home")}
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT)],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert log.read_text().strip() == "hook claude --agent claude-code"
    assert "not reported" not in result.stderr


def test_report_script_uses_gtimeout_when_timeout_is_missing(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    _stub(bin_dir, "gtimeout", "#!/bin/sh\necho \"gtimeout $1\" >> \"%s\"\nshift\nexec \"$@\"\n" % log)
    _stub(bin_dir, "taskman", "#!/bin/sh\necho \"$*\" >> \"%s\"\nexit 0\n" % log)
    env = {"PATH": _bare_path(bin_dir), "HOME": str(tmp_path / "empty-home")}
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT)],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert log.read_text().splitlines() == ["gtimeout 4", "hook claude --agent claude-code"]


def test_a_blank_env_token_does_not_hide_the_user_file_token(tmp_path, monkeypatch):
    user = tmp_path / ".config" / "taskman"
    user.mkdir(parents=True)
    (user / "config.json").write_text(json.dumps({"url": "http://127.0.0.1:8080", "token": "user-secret"}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TASKMAN_ACCESS_TOKEN", "  ")
    assert resolve_settings().token == "user-secret"


def test_saving_the_url_tightens_an_existing_readable_config(tmp_path, monkeypatch):
    user = tmp_path / ".config" / "taskman"
    user.mkdir(parents=True)
    existing = user / "config.json"
    existing.write_text(json.dumps({"token": "user-secret"}))
    existing.chmod(0o644)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = save_server_url("http://127.0.0.1:9090")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"token": "user-secret", "url": "http://127.0.0.1:9090"}
