"""Render an item as Markdown with YAML frontmatter, for `taskman show`."""
from __future__ import annotations

from io import StringIO
from typing import Any

from ruamel.yaml import YAML

_FENCE = "---"


def _new_yaml():
    yaml = YAML(typ="rt")
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def emit_frontmatter(data: dict[str, Any], body: str) -> str:
    """Render ``data`` + ``body`` as a frontmatter-style markdown document."""
    buf = StringIO()
    _new_yaml().dump(data, buf)
    yaml_text = buf.getvalue()
    if not yaml_text.endswith("\n"):
        yaml_text += "\n"
    return f"{_FENCE}\n{yaml_text}{_FENCE}\n\n{body}"
