"""The CLI in remote mode: every command reaches work items through a Taskman server."""
from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from taskman.storage.remote import RemoteStore, remote_settings
from taskman.storage.settings import load_env_file
from taskman.storage.store import StorageConfigError, StorageError, item_data, item_from_data
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


def test_text_output_prints_keys_and_json_keeps_the_location(remote_env):
    assert run("new", "--title", "Task", "--project", "TM") == "TM-1\n"
    assert json.loads(run("new", "--title", "Other", "--project", "TM", "--json")) == {"id": "TM-2", "path": "server:TM-2"}
    assert run("update", "TM-1", "--body-file", "-", input="Spec.\n") == "TM-1\n"
    prepare("TM-1")
    assert run("move", "TM-1", "active") == "TM-1\n"
    assert run("convert", "TM-1", "feat") == "TM-1\n"
    refused = run("move", "TM-2", "active", code=2)  # a draft cannot become active
    assert "Error" in refused and "server:" not in refused


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


def test_list_and_ready_follow_the_order_set_with_reorder_through_the_server(remote_env):
    feat = new("Feature", "feat")
    first, second = new("First", parent=feat), new("Second", parent=feat)
    for key in (feat, first, second):
        run("finalize", key)
    run("reorder", second, "--position", "1")

    def keys(*args):
        return [row["id"] for row in json.loads(run(*args, "--json"))]

    assert keys("list") == keys("ready") == [feat, second, first]
    assert keys("list", "--parent", feat) == [second, first]
    assert [line.split()[2] for line in run("list", "--parent", feat).splitlines() if line.startswith("  ")] == [
        second, first]


@pytest.mark.parametrize(
    "command",
    [["list"], ["ready"], ["show", "TM-1"], ["context", "TM-1"], ["search", "x"], ["validate"],
     ["dependents", "TM-1"], ["waiting-on", "TM-1"], ["activity", "TM-1"], ["browse"]],
)
def test_a_read_command_reports_a_server_error_in_one_line(remote_env, sqlite_env, command):
    new("Task")
    # The server's database loses its items table: every read fails on the server with HTTP 500.
    conn = sqlite3.connect(sqlite_env.sqlite_path)
    try:
        conn.execute("ALTER TABLE items RENAME TO items_gone")
    finally:
        conn.close()
    assert run(*command, code=1) == "Error: The Taskman server failed.\n"


def test_a_read_command_still_reports_a_settings_error_with_exit_2(remote_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_TOKEN", "tmn_wrong")
    output = run("show", "TM-1", code=2)
    assert output.startswith("Error: ") and "Traceback" not in output


def test_a_newer_server_that_adds_item_fields_does_not_break_the_cli(remote_env, monkeypatch):
    from taskman import server

    public = server._public
    monkeypatch.setattr(server, "_public", lambda item: public(item) | {"estimate": 3})
    key = new("Task", body="Spec.\n")
    run("finalize", key)
    assert key in run("list") and key in run("ready")
    assert show(key)["body"] == "Spec.\n"
    assert json.loads(run("context", key))["item"]["id"] == key


def test_an_item_without_a_known_field_is_still_refused(remote_env):
    data = item_data(remote_env.store.get(new("Task")))
    assert item_from_data(data | {"estimate": 3}) == item_from_data(data)
    del data["body"]
    with pytest.raises(TypeError):
        item_from_data(data)


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
    message = f"Error: Not logged in to {remote_env.url}. Set TASKMAN_TOKEN, or run: taskman auth login --server {remote_env.url}\n"
    assert run("list", code=2) == message
    assert run("auth", "status", code=1) == message


def test_a_refused_token_stops_the_command(remote_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_TOKEN", "tmn_wrong")
    message = (f"Error: The Taskman server at {remote_env.url} refused the token: it is unknown, expired or revoked. "
               f"Set TASKMAN_TOKEN to a new token, or run: taskman auth login --server {remote_env.url}\n")
    assert run("list", code=2) == message
    assert run("new", "--title", "T", "--project", "TM", code=2) == message
    assert remote_env.store.items() == []


def test_a_server_that_does_not_answer_stops_the_command(sqlite_env, monkeypatch):
    monkeypatch.setenv("TASKMAN_SERVER_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TASKMAN_TOKEN", "tmn_any")
    output = run("list", code=2)
    assert "Cannot reach the Taskman server" in output and "network settings" not in output


def test_a_proxy_that_refuses_the_host_names_the_network_settings(sqlite_env, monkeypatch):
    class RefusingProxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):  # noqa: N802
            self.send_response(403)
            self.end_headers()

        def log_message(self, *args):
            return

    proxy = HTTPServer(("127.0.0.1", 0), RefusingProxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    for name in ("https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.server_port}")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TASKMAN_SERVER_URL", "https://taskman.example.com")
    monkeypatch.setenv("TASKMAN_TOKEN", "tmn_any")
    try:
        output = run("list", code=2)
    finally:
        proxy.shutdown()
        proxy.server_close()
    assert output == ("Error: Cannot reach the Taskman server at https://taskman.example.com: Tunnel connection failed: "
                      "403 Forbidden. The network settings of this environment must allow taskman.example.com.\n")


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


_DATABASE_COMMANDS = (["list"], ["db", "upgrade"], ["serve"], ["token", "list"])


def test_a_file_that_is_not_a_database_is_reported_in_one_line(sqlite_env, tmp_path, monkeypatch):
    path = tmp_path / "garbage.db"
    path.write_text("{bad")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{path}")
    for command in _DATABASE_COMMANDS:
        assert run(*command, code=2) == "Error: Cannot open the Taskman database: file is not a database\n"
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{sqlite_env.sqlite_path}")
    assert run("list").strip() == "(no items)"


def test_db_upgrade_reports_a_folder_it_cannot_make_in_one_line(sqlite_env, tmp_path, monkeypatch):
    blocker = tmp_path / "plainfile"
    blocker.write_text("x")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{blocker}/taskman.db")
    assert run("db", "upgrade", code=2) == f"Error: Cannot open the Taskman database: File exists: {blocker}\n"
    path = tmp_path / "new" / "folder" / "taskman.db"
    monkeypatch.setenv("TASKMAN_DATABASE_URL", f"sqlite://{path}")
    run("db", "upgrade")
    assert path.is_file()


@pytest.mark.parametrize("url, secrets", [
    ("postgresql://taskman:secretpw@127.0.0.1:1/unreachable_test", ["secretpw"]),
    ("postgresql://taskman:secret pw@127.0.0.1:1/unreachable_test", ["secret pw"]),
    # libpq ends the user info at the first "@" or "/": the rest of the password goes into the host or database name.
    ("postgresql://taskman:Tr0ub@dor3@127.0.0.1:1/unreachable_test", ["Tr0ub", "dor3"]),
    ("postgresql://taskman:first@second/third@127.0.0.1:1/unreachable_test", ["first", "second", "third"]),
    ("postgresql://taskman@127.0.0.1:1/unreachable_test?password=secret pw", ["secret pw"]),
    ("postgresql://taskman@127.0.0.1:1/unreachable_test?sslmode=disable&password=secret%zzpw", ["secret"]),
])
def test_a_postgres_database_that_cannot_be_opened_is_reported_without_the_url(sqlite_env, monkeypatch, url, secrets):
    pytest.importorskip("psycopg")
    monkeypatch.setenv("TASKMAN_BACKEND", "postgres")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", url)
    for command in _DATABASE_COMMANDS:
        output = run(*command, code=2)
        assert output.startswith("Error: Cannot open the Taskman database: ") and output.count("\n") == 1
        assert url not in output and "Traceback" not in output
        assert [secret for secret in secrets if secret in output] == [], output


def test_the_user_name_and_a_query_with_an_at_sign_do_not_hide_the_driver_reason(sqlite_env, monkeypatch):
    pytest.importorskip("psycopg")
    monkeypatch.setenv("TASKMAN_BACKEND", "postgres")
    monkeypatch.setenv("TASKMAN_DATABASE_URL", "postgresql://u@127.0.0.1:1/unreachable_test?password=secret@pw")
    output = run("list", code=2)
    assert 'connection to server at "127.0.0.1", port 1 failed: Connection refused' in output
    assert "secret" not in output


def test_serve_reports_an_address_it_cannot_use(sqlite_env, monkeypatch):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        output = run("serve", "--port", port, code=2)
    finally:
        busy.close()
    assert output == f"Error: Taskman cannot listen on 127.0.0.1:{port}: Address already in use\n"
    output = run("serve", "--host", "203.0.113.9", "--port", "0", code=2)
    assert output.startswith("Error: Taskman cannot listen on 203.0.113.9:0: ") and output.count("\n") == 1
    for port in ("70000", "-1"):
        output = run("serve", "--port", port, code=2)
        assert output == f"Error: Taskman cannot listen on 127.0.0.1:{port}: bind(): port must be 0-65535.\n"
    monkeypatch.setenv("PORT", "70000")
    assert run("serve", code=2) == "Error: Taskman cannot listen on 127.0.0.1:70000: bind(): port must be 0-65535.\n"


def test_serve_listens_on_the_port_variable_and_stops_on_sigterm(sqlite_env):
    environment = {**os.environ, "PYTHONPATH": SRC, "PORT": "0"}
    process = subprocess.Popen([sys.executable, "-m", "taskman.cli", "serve"], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        first = process.stdout.readline()
        assert "Browser login is off" in first
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
