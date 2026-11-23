"""`taskman locate`: the item of a key or a link, checked against the git checkout of the current directory."""
from __future__ import annotations

import json
import subprocess

import pytest

from tests.sql_helpers import new, run


@pytest.fixture(params=["sqlite_env", "remote_env"])
def env(request):
    return request.getfixturevalue(request.param)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A git checkout whose origin is github.com/vkroz/taskman, as the current directory."""
    folder = tmp_path / "checkout"
    folder.mkdir()
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    subprocess.run(["git", "-C", str(folder), "remote", "add", "origin", "git@github.com:vkroz/taskman.git"], check=True)
    subprocess.run(["git", "-C", str(folder), "remote", "add", "local", str(tmp_path / "elsewhere")], check=True)
    monkeypatch.chdir(folder)
    return folder


def test_a_key_in_a_checkout_of_an_inherited_repository_matches(env, checkout):
    feat = new("Feature", "feat")
    task = new("Task", parent=feat)
    run("repo", feat, "https://github.com/vkroz/taskman", "github.com/vkroz/other")
    result = json.loads(run("locate", task))
    assert result["key"] == task
    assert result["repositories"] == ["github.com/vkroz/other", "github.com/vkroz/taskman"]
    assert (result["inherited_from"], result["match"]) == (feat, "github.com/vkroz/taskman")
    # A remote that is a local path is not an address, so it is left out.
    assert result["remotes"] == ["github.com/vkroz/taskman"]


def test_a_checkout_of_another_repository_is_refused(env, checkout):
    key = new("Task")
    run("repo", key, "github.com/vkroz/reservoir")
    output = run("locate", key, code=1)
    assert "this directory (github.com/vkroz/taskman) is not a checkout of a repository of" in output
    assert "github.com/vkroz/reservoir" in output


def test_an_item_without_repositories_is_located_with_an_empty_list(env, checkout):
    key = new("Task")
    result = json.loads(run("locate", key))
    assert (result["repositories"], result["match"], result["inherited_from"]) == ([], None, None)


def test_a_directory_outside_git_has_no_remotes(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    key = new("Task")
    run("repo", key, "github.com/vkroz/taskman")
    assert "(no git remote)" in run("locate", key, code=1)


def test_a_link_of_this_server_gives_its_item(remote_env, checkout):
    key = new("Task")
    run("repo", key, "github.com/vkroz/taskman")
    link = f"{remote_env.url.upper().replace('HTTP://', 'http://')}/items/{key}"
    result = json.loads(run("locate", link))
    assert (result["key"], result["server"], result["match"]) == (key, remote_env.url, "github.com/vkroz/taskman")


def test_a_link_of_another_server_is_refused(remote_env, checkout):
    key = new("Task")
    output = run("locate", f"https://taskman.example.com/items/{key}", code=1)
    assert "the link names the server https://taskman.example.com, but this CLI works with" in output


def test_a_link_without_a_server_set_is_refused(sqlite_env, checkout):
    key = new("Task")
    output = run("locate", f"https://taskman.example.com/items/{key}", code=1)
    assert "this CLI uses a local database" in output


def test_an_unknown_key_is_not_found(env, checkout):
    assert "item not found: TM-9" in run("locate", "TM-9", code=1)


def test_an_explicit_default_port_names_the_same_server(remote_env, checkout, monkeypatch):
    from taskman.commands.locate import _origin

    assert _origin("https://Taskman.Example.com:443") == _origin("https://taskman.example.com")
    assert _origin("http://h:80") == "http://h" and _origin("http://h:8080") == "http://h:8080"
    assert _origin("http://h:443") == "http://h:443"


def test_a_link_with_an_invalid_port_is_an_error(remote_env, checkout):
    output = run("locate", "http://127.0.0.1:99999/items/TM-1", code=1)
    assert "the link has an invalid port" in output and "Traceback" not in output
