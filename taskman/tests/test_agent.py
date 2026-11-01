"""One agent-name resolver for the CLI and the hooks."""
from __future__ import annotations

from taskman.agent import default_agent, resolve_agent


def test_flag_then_env_then_fallback_then_default(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("TASKMAN_AGENT", " codex ")
    assert resolve_agent(" flag ") == "flag"
    assert resolve_agent("  ", fallback="git") == "codex"
    monkeypatch.setenv("TASKMAN_AGENT", "   ")
    assert resolve_agent("", fallback=" git ") == "git"
    assert resolve_agent(None) == default_agent()
    assert resolve_agent("") != ""


def test_claude_code_sessions_default_to_the_hook_name(monkeypatch):
    monkeypatch.delenv("TASKMAN_AGENT", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert resolve_agent("") == "claude-code"
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setattr("taskman.agent.socket.gethostname", lambda: "laptop.local")
    assert resolve_agent("") == "laptop"
    monkeypatch.setattr("taskman.agent.socket.gethostname", lambda: "")
    assert resolve_agent("") == "taskman"
