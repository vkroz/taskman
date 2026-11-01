"""The CLI is an HTTP client of the server."""
from __future__ import annotations

import json
import os
import threading
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
    env.update({"TASKMAN_URL": base, "TASKMAN_ACCESS_TOKEN": TOKEN, "TASKMAN_AGENT": "codex"})
    return env


def test_create_pull_and_complete_round_trip(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    created = runner.invoke(
        app,
        ["new", "--title", "Implement pull", "--type", "task", "--body-file", "-", "--json"],
        input="Acceptance: the file exists.\n",
        env=_env(server),
    )
    assert created.exit_code == 0, created.output
    task = json.loads(created.output)
    pulled = runner.invoke(app, ["pull", str(task["id"]), "--agent", "codex", "--session", "sess"], env=_env(server))
    assert pulled.exit_code == 0, pulled.output
    copy = tmp_path / ".taskman" / "tasks" / f"{task['id']}.md"
    assert copy.is_file()
    text = copy.read_text()
    assert "Implement pull" in text
    assert "Acceptance: the file exists." in text
    assert (tmp_path / ".taskman" / "active").read_text().strip() == str(task["id"])
    shown = runner.invoke(app, ["show", str(task["id"]), "--json"], env=_env(server))
    assert json.loads(shown.output)["status"] == "active"
    finished = runner.invoke(
        app,
        ["complete", str(task["id"]), "--message", "shipped", "--agent", "codex", "--session", "sess"],
        env=_env(server),
    )
    assert finished.exit_code == 0, finished.output
    again = runner.invoke(
        app,
        ["complete", str(task["id"]), "--message", "shipped", "--agent", "codex", "--session", "sess", "--json"],
        env=_env(server),
    )
    assert json.loads(again.output)["status"] == "done"
    board = runner.invoke(app, ["activity", "--json"], env=_env(server))
    row = json.loads(board.output)["rows"][0]
    assert row["last_event"] == "completion"
    assert row["agent"] == "codex"


def test_wrong_token_is_rejected_by_the_cli(server):
    result = runner.invoke(
        app,
        ["list", "--json"],
        env={"TASKMAN_URL": server, "TASKMAN_ACCESS_TOKEN": "nope"},
    )
    assert result.exit_code == 1
    assert "Authentication required" in (result.output + (result.stderr or ""))


def test_pull_defaults_a_non_empty_agent(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = _env(server)
    env.pop("TASKMAN_AGENT", None)
    created = runner.invoke(app, ["new", "--title", "Named agent", "--json"], env=env)
    assert created.exit_code == 0, created.output
    task_id = str(json.loads(created.output)["id"])
    pulled = runner.invoke(app, ["pull", task_id], env=env)
    assert pulled.exit_code == 0, pulled.output
    shown = json.loads(runner.invoke(app, ["context", task_id], env=env).output)
    assert shown["events"][0]["kind"] == "pull"
    assert shown["events"][0]["agent"]


def test_complete_and_report_pr_default_a_non_empty_agent(server, tmp_path, monkeypatch):
    from taskman.agent import default_agent

    monkeypatch.chdir(tmp_path)
    env = _env(server)
    env.pop("TASKMAN_AGENT", None)
    created = runner.invoke(app, ["new", "--title", "Named agent", "--json"], env=env)
    assert created.exit_code == 0, created.output
    task_id = str(json.loads(created.output)["id"])
    finished = runner.invoke(app, ["complete", task_id, "--message", "shipped"], env=env)
    assert finished.exit_code == 0, finished.output
    reported = runner.invoke(
        app,
        ["report", "pr", "--task", task_id, "--url", "https://example.com/pull/7", "--agent", "   "],
        env=env,
    )
    assert reported.exit_code == 0, reported.output
    shown = json.loads(runner.invoke(app, ["context", task_id], env=env).output)
    agents = {event["kind"]: event["agent"] for event in shown["events"]}
    assert agents["completion"] == default_agent()
    assert agents["pull_request"] == default_agent()
    assert "" not in agents.values()


def test_validate_reports_ok(server):
    result = runner.invoke(app, ["validate"], env=_env(server))
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "OK"


def test_serve_execs_gunicorn_on_all_interfaces(database, monkeypatch):
    import os

    calls = []
    monkeypatch.setattr("taskman.commands.serve._exec", lambda program, argv: calls.append(argv))
    result = runner.invoke(
        app,
        ["serve"],
        env={
            "TASKMAN_ACCESS_TOKEN": TOKEN,
            "TASKMAN_DATABASE_URL": database,
            "PORT": "9001",
            "TASKMAN_WEB_WORKERS": "1",
            "PATH": os.environ.get("PATH", ""),
        },
    )
    assert result.exit_code == 0, result.output
    assert "0.0.0.0:9001" in calls[0]
    assert "taskman.httpapp:application" in calls[0]
    assert TOKEN not in " ".join(calls[0])


def test_serve_refuses_to_start_without_a_token(database):
    result = runner.invoke(app, ["serve"], env={"TASKMAN_DATABASE_URL": database, "TASKMAN_ACCESS_TOKEN": "  "})
    text = (result.output or "") + (result.stderr or "")
    assert result.exit_code == 1
    assert "TASKMAN_ACCESS_TOKEN" in text


def test_cli_rejects_non_ascii_and_out_of_range_ids_without_a_traceback(server):
    for value in ("²", "99999999999999999999", "0"):
        result = runner.invoke(app, ["show", value], env=_env(server))
        assert result.exit_code == 1, (value, result.output)
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Task id must be a positive integer" in (result.output + (result.stderr or ""))


def test_validate_names_an_unavailable_database(server, monkeypatch):
    def fail():
        raise RuntimeError("postgres is down")

    monkeypatch.setattr("taskman.httpapp.ping", fail)
    result = runner.invoke(app, ["validate"], env=_env(server))
    assert result.exit_code == 1
    assert "database is unavailable" in (result.output + (result.stderr or ""))
