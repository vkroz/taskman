"""Browser test of the token page: a person revokes their own token with the page's button.

The page has a strict Content-Security-Policy and sends no referrer. This test
shows that a real browser still posts the revoke form. It skips, or fails with
``TASKMAN_TEST_REQUIRE_BROWSER=1``, as the other browser tests do.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from taskman.storage.accounts import create_token, list_tokens
from tests.conftest import RunningServer
from tests.test_login import FakeGoogle, get, settings
from tests.ui.test_workspace_ui import chromium  # noqa: F401 - the shared browser fixture

pytestmark = pytest.mark.ui


def _person(server, token: str) -> str | None:
    request = urllib.request.Request(f"{server.url}/api/v1/whoami")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())["person"]
    except urllib.error.HTTPError:
        return None


def test_a_person_revokes_an_own_token_on_the_token_page(sqlite_env, chromium):  # noqa: F811
    google = FakeGoogle()
    server = RunningServer(sqlite_env, login=settings(), post_form=google.post_form)
    context = chromium.new_context()
    try:
        laptop_id, laptop = create_token(server.store, "ada@example.com", "laptop", days=90)
        _, headers, _ = get(f"{server.url}/auth/token")
        query = google.sign_in(headers["Location"])
        page = context.new_page()
        messages: list[str] = []
        page.on("console", lambda message: messages.append(message.text))
        page.goto(f"{server.url}/auth/callback?state={query['state']}&code={query['code']}")
        row = page.locator("tr", has=page.locator("td", has_text="laptop"))
        row.get_by_role("button", name="Revoke").click()
        page.get_by_text(f"Revoked token {laptop_id}.").wait_for()
        assert page.locator("tr", has=page.locator("td", has_text="laptop")).get_by_role("button").count() == 0
        assert _person(server, laptop) is None
        assert list_tokens(server.store, "ada@example.com")[-1]["revoked_at"] is None
        assert not [text for text in messages if "Content Security Policy" in text]
    finally:
        context.close()
        server.stop()
