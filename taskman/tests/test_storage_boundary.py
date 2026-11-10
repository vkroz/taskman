"""Commands reach work items only through the storage interface."""
from __future__ import annotations

import ast
from pathlib import Path

import taskman.commands

FILE_LAYOUT_MODULES = {"taskman.model.layout", "taskman.model.names", "taskman.model.yaml_io", "taskman.model.locking"}


def _imports(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def test_commands_do_not_import_file_layout_modules():
    commands_dir = Path(taskman.commands.__file__).parent
    offenders = {
        path.name: sorted(_imports(path) & FILE_LAYOUT_MODULES)
        for path in sorted(commands_dir.glob("*.py"))
        if _imports(path) & FILE_LAYOUT_MODULES
    }
    assert offenders == {}


def test_boundary_check_detects_an_offending_import(tmp_path):
    offender = tmp_path / "offender.py"
    offender.write_text("from taskman.model import layout\nfrom taskman.model.names import parse_name\n")
    assert _imports(offender) & FILE_LAYOUT_MODULES == {"taskman.model.layout", "taskman.model.names"}
