import json
import subprocess

from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands import install as module

runner = CliRunner()


def test_install_two_hosts_uses_same_local_source_without_shell(monkeypatch, tmp_path):
    root = tmp_path / "a checkout with spaces"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/marketplace.json").write_text(json.dumps({"name": "taskman", "plugins": [{"name": "tm"}]}))
    calls = []
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd)

    def run(args, **kwargs):
        assert not kwargs.get("shell")
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "[]" if "list" in args else "ok", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    result = runner.invoke(app, ["install", "--platform", "codex", "--platform", "claude-code", "--source", str(root)])
    assert result.exit_code == 0, result.output
    assert [c[-1] for c in calls if c[1:4] == ["plugin", "marketplace", "add"]] == [str(root.resolve()), str(root.resolve())]
    assert ["codex", "plugin", "add", "tm@taskman"] in calls
    assert ["claude", "plugin", "install", "tm@taskman", "--scope", "user"] in calls
    assert ["claude", "plugin", "update", "tm@taskman", "--scope", "user"] in calls


def test_missing_host_is_skipped_and_the_other_host_is_installed(monkeypatch):
    calls = []
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd if cmd == "claude" else None)

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "[]" if "list" in args else "ok", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    result = runner.invoke(app, ["install", "--platform", "codex", "--platform", "claude-code"])
    text = (result.output or "") + (result.stderr or "")
    assert result.exit_code == 0, text
    assert "Installed Taskman plugin for claude-code." in text
    assert "Skipped codex" in text
    assert "codex is not on PATH" in text
    assert "taskman install --platform codex" in text
    assert all(call[0] != "codex" for call in calls)
    assert ["claude", "plugin", "install", "tm@taskman", "--scope", "user"] in calls


def test_every_missing_host_is_reported_without_running_commands(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = runner.invoke(app, ["install", "--platform", "codex", "--platform", "claude-code"])
    text = (result.output or "") + (result.stderr or "")
    assert result.exit_code == 0, text
    assert "Skipped codex" in text
    assert "Skipped claude-code" in text
    assert "No plugins registered" in text


def test_wrong_existing_source_is_not_silently_reused(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([{"name": "taskman", "path": "/old/checkout"}]), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    result = runner.invoke(app, ["install", "--platform", "claude-code"])
    assert result.exit_code == 2
    assert all("list" in call for call in calls)
    assert "Installed Taskman" not in result.output


def test_install_failure_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0 if "list" in args else 1, "[]", "host failure"),
    )
    result = runner.invoke(app, ["install", "--platform", "codex"])
    assert result.exit_code == 2
    assert "Installed Taskman" not in result.output


def test_server_url_is_saved_for_local_use(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["install", "--server", "http://127.0.0.1:8080"])
    assert result.exit_code == 0, result.output
    saved = json.loads((tmp_path / ".config" / "taskman" / "config.json").read_text())
    assert saved["url"] == "http://127.0.0.1:8080"
    assert "token" not in saved


def test_help_describes_the_server(monkeypatch):
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "Postgres" in result.output
    assert "TASKMAN_ACCESS_TOKEN" in result.output
    assert "not imported" in result.output
