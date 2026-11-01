"""--agent names the author of the changes. It comes only from the command line."""
from __future__ import annotations

import pytest

from taskman.agent import AGENT_REQUIRED, MissingAgent, require_agent


def test_the_author_is_trimmed():
    assert require_agent("  Ada Lovelace \t") == "Ada Lovelace"
    assert require_agent("claude-code") == "claude-code"


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_a_missing_or_blank_author_is_rejected(value):
    with pytest.raises(MissingAgent) as exc:
        require_agent(value)
    assert str(exc.value) == AGENT_REQUIRED == "--agent <author> is required: name who authored these changes"


def test_the_environment_is_never_consulted(monkeypatch):
    for name in ("CLAUDECODE", "USER", "HOSTNAME"):
        monkeypatch.setenv(name, "from-env")
    with pytest.raises(MissingAgent):
        require_agent("")
