"""The browser tests skip without Playwright or Chromium, and fail instead with
``TASKMAN_TEST_REQUIRE_BROWSER=1``, as in CI. Each case runs one browser test in
its own pytest process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _without_playwright(tmp_path: Path) -> dict[str, str]:
    """A ``playwright`` package with no ``sync_api``, ahead of the installed one."""
    (tmp_path / "playwright").mkdir()
    (tmp_path / "playwright" / "__init__.py").write_text("")
    return {"PYTHONPATH": os.pathsep.join(filter(None, [str(tmp_path), os.environ.get("PYTHONPATH")]))}


def _without_chromium(tmp_path: Path) -> dict[str, str]:
    """Playwright looks for its browsers in an empty folder."""
    pytest.importorskip("playwright.sync_api")
    return {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "no-browsers")}


MISSING = pytest.mark.parametrize("missing", [_without_playwright, _without_chromium], ids=["playwright", "chromium"])


def _run(changes: dict[str, str], require: bool) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "TASKMAN_TEST_REQUIRE_BROWSER"}
    env.update(changes)
    if require:
        env["TASKMAN_TEST_REQUIRE_BROWSER"] = "1"
    command = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
               "tests/ui/test_workspace_ui.py", "-k", "test_views_and_counts"]
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


@MISSING
def test_browser_tests_skip_without_a_browser(tmp_path, missing) -> None:
    result = _run(missing(tmp_path), require=False)
    # A module that skips at import leaves no test to collect.
    assert result.returncode in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED), result.stdout
    assert "1 skipped" in result.stdout


@MISSING
def test_browser_tests_fail_without_a_browser_when_required(tmp_path, missing) -> None:
    result = _run(missing(tmp_path), require=True)
    assert result.returncode in (pytest.ExitCode.TESTS_FAILED, pytest.ExitCode.INTERRUPTED), result.stdout
    assert "(TASKMAN_TEST_REQUIRE_BROWSER=1)" in result.stdout
    assert "skipped" not in result.stdout
    assert "During handling of the above exception" not in result.stdout
