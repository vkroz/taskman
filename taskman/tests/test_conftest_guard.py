"""The test fixtures refuse a non-test database and fail instead of skipping in CI."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load conftest by path; the tests directory is not an importable package.
_spec = importlib.util.spec_from_file_location("_taskman_conftest", Path(__file__).with_name("conftest.py"))
_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conftest)
_unavailable = _conftest._unavailable
database_name_error = _conftest.database_name_error


def test_a_database_name_without_the_test_suffix_is_refused():
    assert database_name_error("postgresql://t:t@127.0.0.1:5432/taskman_test") is None
    refusal = database_name_error("postgresql://t:t@127.0.0.1:5432/taskman")
    assert refusal and "_test" in refusal
    assert database_name_error("postgresql://t:t@127.0.0.1:5432/taskman_test_backup")
    assert database_name_error("postgresql://t:t@127.0.0.1:5432/")


def test_an_unreachable_database_fails_when_an_override_or_ci_is_set(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TASKMAN_TEST_DATABASE_URL", "postgresql://t:t@127.0.0.1:1/x_test")
    with pytest.raises(pytest.fail.Exception):
        _unavailable("down")
    monkeypatch.delenv("TASKMAN_TEST_DATABASE_URL")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception):
        _unavailable("down")


def test_an_unreachable_database_skips_for_a_plain_local_run(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TASKMAN_TEST_DATABASE_URL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _unavailable("down")
