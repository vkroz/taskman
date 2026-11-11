"""Check 15 of the UI smoke suite: a static lint of the workspace styles. It needs no browser."""
from __future__ import annotations

import re

import pytest

from taskman.commands.ui import render_page

pytestmark = pytest.mark.ui


def test_workspace_styles_use_tokens_and_scales() -> None:
    """Colours come from the :root tokens; type, weight and radius stay on their scales."""
    html = render_page()
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    outside_tokens = re.sub(r":root[^{]*\{[^{}]*\}", "", html)
    assert re.findall(r"#[0-9a-fA-F]{3,8}(?![\w-])", outside_tokens) == []
    assert set(re.findall(r"font-size:\s*([^;]+);", css)) <= {"12px", "13px", "14px", "16px", "20px"}
    assert set(re.findall(r"font-weight:\s*([^;]+);", css)) <= {"400", "500", "600"}
    assert set(re.findall(r"border-radius:\s*([^;]+);", css)) <= {"var(--r1)", "var(--r2)", "var(--r3)", "999px"}
    assert re.findall(r"--r(\d):\s*(\d+)px;", css) == [("1", "4"), ("2", "6"), ("3", "8")]
    assert "color-scheme: light dark;" in css and "prefers-color-scheme: dark" in css
