"""The CLI in remote mode: every command reaches work items through a Taskman server."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from taskman.storage.remote import RemoteStore, remote_settings
from taskman.storage.settings import load_env_file
from taskman.storage.store import StorageConfigError, StorageError
from tests.sql_helpers import comment, new, prepare, run, show

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_a_work_item_goes_from_new_to_done_through_the_server(remote_env):
    key = new("Remote task", body="Do the work.\n")
    assert key == "TM-1"
    assert show(key)["path"] == "server:TM-1"
    prepare(key)
    run("move", key, "active", "--actor", "claude")
    run("close", key)
    assert show(key)["frontmatter"]["status"] == "done"
    assert key in run("list", "--status", "done")
    activity = run("activity", key)
    assert "claude for Vlad" in activity and "Vlad" in activity
    assert remote_env.store.get(key).status == "done"


def test_the_person_of_the_token_is_the_author(remote_env):
    key = new("Task")
    assert show(key)["frontmatter"]["created_by"] == "Vlad"
    comment(key, "decision", "Chose X.", actor="codex")
    assert show(key)["frontmatter"]["journal"][0]["actor"] == "codex for Vlad"
    run("comment", key, "--kind", "progress", "--body-file", "-", input="Half done.")
    assert show(key)["frontmatter"]["journal"][1]["actor"] == "Vlad"


def test_errors_and_exit_codes_match_direct_use(remote_env):
    key = new("Task")
    assert "finalize the draft" in run("close", key, code=2)
    run("assign", key, "codex")
    assert "No change" in run("assign", key, "codex", code=0)
    assert "item not found" in run("show", "TM-9", code=1)
    assert "Position must be between" in run("reorder", key, "--position", "9", code=None)


def test_read_commands_work_through_the_server(remote_env):
    feat = new("Feature", "feat")
    task = new("Task", parent=feat)
    assert task in run("list")
    assert run("validate").strip() == "OK"
    assert "title: Task" in run("show", task)
    assert json.loads(run("context", task))["item"]["id"] == task
    run("reorder", task, "--position", "1")
    assert task in run("search", "Task")


def test_the_cli_opens_no_database_in_remote_mode(remote_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_BACKEND", "postgres")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    assert new("Task") == "TM-1"


def test_two_processes_in_different_directories_share_one_project(remote_env, tmp_path):
    environment = {**os.environ, "PYTHONPATH": SRC}

    def taskman(directory: str, *args: str) -> str:
        cwd = tmp_path / directory
        cwd.mkdir(exist_ok=True)
        done = subprocess.run([sys.executable, "-m", "taskman.cli", *args], cwd=cwd, env=environment,
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout

    first = json.loads(taskman("one", "new", "--title", "From one", "--project", "TM", "--json"))["id"]
    second = json.loads(taskman("two", "new", "--title", "From two", "--project", "TM", "--json"))["id"]
    assert (first, second) == ("TM-1", "TM-2")
    assert "From two" in taskman("one", "list") and "From one" in taskman("two", "list")


# ---- settings -------------------------------------------------------------


def test_a_missing_token_stops_the_command(remote_env, monkeypatch):
    monkeypatch.delenv("TASKMAN_TOKEN")
    assert "TASKMAN_TOKEN is not set" in run("list", code=2)


def test_a_refused_token_stops_the_command(remote_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_TOKEN", "tmn_wrong")
    assert "refused the token" in run("list", code=2)
    assert "refused the token" in run("new", "--title", "T", "--project", "TM", code=2)
    assert remote_env.store.items() == []


def test_a_server_that_does_not_answer_stops_the_command(sqlite_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_SERVER_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TASKMAN_TOKEN", "tmn_any")
    assert "Cannot reach the Taskman server" in run("list", code=2)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://taskman.example.com", "must use https://"),
        ("ftp://taskman.example.com", "http:// or https://"),
        ("taskman.example.com", "http:// or https://"),
    ],
)
def test_a_token_goes_over_https_or_to_this_machine_only(url, message):
    with pytest.raises(StorageConfigError, match=message):
        remote_settings({"TASKMAN_SERVER_URL": url, "TASKMAN_TOKEN": "tmn_any"})
    assert remote_settings({"TASKMAN_SERVER_URL": "https://taskman.example.com/", "TASKMAN_TOKEN": "t"}) == (
        "https://taskman.example.com", "t")


def test_a_redirect_is_not_followed():
    seen = []

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(StorageConfigError, match="does not follow redirects"):
            RemoteStore(f"http://127.0.0.1:{server.server_port}", "tmn_secret").items()
    finally:
        server.shutdown()
        server.server_close()
    assert seen == ["Bearer tmn_secret"]


def test_a_repository_env_file_cannot_name_the_server_or_the_token(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        "TASKMAN_SERVER_URL=https://evil.example.com\nTASKMAN_TOKEN=tmn_planted\nTASKMAN_BACKEND=sqlite\n"
    )
    environ: dict[str, str] = {}
    load_env_file(tmp_path, environ)
    assert environ == {"TASKMAN_BACKEND": "sqlite"}
    warning = capsys.readouterr().err
    assert "ignores TASKMAN_SERVER_URL" in warning and "ignores TASKMAN_TOKEN" in warning
    assert "tmn_planted" not in warning and "evil.example.com" not in warning


def test_a_remote_store_offers_no_direct_writes(remote_env):
    store = RemoteStore(remote_env.url, remote_env.token)
    for write in (lambda: store.save("TM-1", {}, ""), lambda: store.set_status("TM-1", "done"),
                  lambda: store.delete("TM-1"), lambda: store.save_order("root:TM", [], "TM-1")):
        with pytest.raises(StorageError, match="operations only"):
            write()


# ---- server-side commands -------------------------------------------------


def test_token_commands_create_list_and_revoke(sqlite_env, monkeypatch):
    output = run("token", "create", "--person", "Ada", "--label", "laptop", "--days", "30")
    token = output.strip().splitlines()[-1]
    assert token.startswith("tmn_") and "valid for 30 days" in output
    listing = run("token", "list")
    assert "Ada" in listing and "laptop" in listing and token not in listing
    rows = json.loads(run("token", "list", "--json"))
    assert rows[0]["person"] == "Ada" and token not in json.dumps(rows)

    from tests.conftest import RunningServer

    running = RunningServer(sqlite_env, person="Other")
    try:
        monkeypatch.setenv("TASKMAN_SERVER_URL", running.url)
        monkeypatch.setenv("TASKMAN_TOKEN", token)
        assert new("By Ada") == "TM-1"
        assert show("TM-1")["frontmatter"]["created_by"] == "Ada"
        run("token", "revoke", rows[0]["id"])
        assert "refused the token" in run("list", code=2)
    finally:
        running.stop()
    assert "no active token" in run("token", "revoke", rows[0]["id"], code=1)
    assert "revoked" in run("token", "list")


def test_token_create_reports_wrong_input(sqlite_env):
    assert "1 to 60 characters" in run("token", "create", "--person", " ", code=1)
    assert "1 day or more" in run("token", "create", "--person", "Ada", "--days", "0", code=1)


def test_server_commands_need_a_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{tmp_path / 'missing.db'}")
    assert "taskman db upgrade" in run("token", "list", code=2)
    assert "taskman db upgrade" in run("serve", code=2)
    monkeypatch.setenv("PORT", "eighty")
    assert "PORT must be a number" in run("serve", code=2)


def test_serve_listens_on_the_port_variable_and_stops_on_sigterm(sqlite_env):
    environment = {**os.environ, "PYTHONPATH": SRC, "PORT": "0"}
    process = subprocess.Popen([sys.executable, "-m", "taskman.cli", "serve"], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        assert "Taskman server listens on http://127.0.0.1:" in line
        url = line.split()[4].rstrip("/")
        with urllib.request.urlopen(url + "/healthz", timeout=10) as response:
            assert json.loads(response.read()) == {"ok": True}
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
