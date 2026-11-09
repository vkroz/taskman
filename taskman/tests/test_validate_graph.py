"""Integration tests for validate's depends_on graph checks."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from taskman.cli import app
from taskman.model.names import parse_name

runner = CliRunner()


def _env(tasks_dir: Path) -> dict[str, str]:
    return {"TASKMAN_TASKS_DIR": str(tasks_dir)}


def _new(tmp_path: Path, title: str) -> str:
    r = runner.invoke(app, ["new", "--title", title], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    return parse_name(Path(r.output.strip()).name).item_id


def _set_depends_on(tmp_path: Path, item_id: str, deps: list[str] | object) -> None:
    """Mutate an item's frontmatter to add a depends_on list (or non-list value)."""
    from taskman.model.layout import find_item_by_id
    from taskman.model.names import special_file_name
    from taskman.model.yaml_io import read_file, write_file

    path = find_item_by_id(tmp_path, item_id)
    assert path is not None
    body_file = path / special_file_name(item_id) if path.is_dir() else path
    data, body = read_file(body_file)
    data["depends_on"] = deps
    write_file(body_file, data, body)


def test_validate_clean_with_no_depends(tmp_path: Path) -> None:
    _new(tmp_path, "A")
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code == 0


def test_validate_clean_with_valid_depends(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends_on(tmp_path, b, [a])
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code == 0


def test_validate_unknown_dependency_warns(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    _set_depends_on(tmp_path, a, ["999999999"])
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    # warning does not block exit 0
    assert r.exit_code == 0
    assert "unknown_dependency" in r.output
    assert "999999999" in r.output


def test_validate_simple_cycle_errors(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends_on(tmp_path, a, [b])
    _set_depends_on(tmp_path, b, [a])
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "dependency_cycle" in r.output


def test_validate_transitive_cycle_errors(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    c = _new(tmp_path, "C")
    _set_depends_on(tmp_path, a, [b])
    _set_depends_on(tmp_path, b, [c])
    _set_depends_on(tmp_path, c, [a])
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code != 0
    assert "dependency_cycle" in r.output


def test_validate_malformed_depends_on_warns(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    _set_depends_on(tmp_path, a, "not_a_list")
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    # warning only, not error
    assert r.exit_code == 0
    assert "malformed_depends_on" in r.output


def test_validate_json_includes_graph_findings(tmp_path: Path) -> None:
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    _set_depends_on(tmp_path, a, [b])
    _set_depends_on(tmp_path, b, [a])
    r = runner.invoke(app, ["validate", "--json"], env=_env(tmp_path))
    data = json.loads(r.output)
    assert data["ok"] is False
    codes = {f["code"] for f in data["findings"]}
    assert "dependency_cycle" in codes


def test_validate_handles_multi_edge_graph(tmp_path: Path) -> None:
    """Item with multiple deps, all valid → no findings."""
    a = _new(tmp_path, "A")
    b = _new(tmp_path, "B")
    c = _new(tmp_path, "C")
    _set_depends_on(tmp_path, c, [a, b])
    r = runner.invoke(app, ["validate"], env=_env(tmp_path))
    assert r.exit_code == 0
