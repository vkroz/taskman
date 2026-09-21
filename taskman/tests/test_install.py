import json
import subprocess

from typer.testing import CliRunner

from taskman.cli import app
from taskman.commands import install as module


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
    result = CliRunner().invoke(app, ["install", "--platform", "codex", "--platform", "claude-code", "--source", str(root)])
    assert result.exit_code == 0, result.output
    assert [c[-1] for c in calls if c[1:4] == ["plugin", "marketplace", "add"]] == [str(root), str(root)]
    assert ["codex", "plugin", "add", "tm@taskman"] in calls
    assert ["claude", "plugin", "install", "tm@taskman", "--scope", "user"] in calls
    assert ["claude", "plugin", "update", "tm@taskman", "--scope", "user"] in calls


def test_missing_host_fails_before_installing_anything(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd if cmd == "codex" else None)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = CliRunner().invoke(app, ["install", "--platform", "codex", "--platform", "claude-code"])
    assert result.exit_code != 0
    assert "install claude" in result.output


def test_wrong_existing_source_is_not_silently_reused(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([{"name": "taskman", "path": "/old/checkout"}]), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    result = CliRunner().invoke(app, ["install", "--platform", "claude-code"])
    assert result.exit_code != 0
    assert all("list" in call for call in calls)


def test_install_failure_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda cmd: cmd)
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0 if "list" in args else 1, "[]", "host failure"))
    result = CliRunner().invoke(app, ["install", "--platform", "codex"])
    assert result.exit_code != 0
    assert "Installed Taskman" not in result.output
