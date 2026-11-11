"""Integration tests for validate's depends_on graph checks."""
from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

from tests import sql_helpers as sql

runner = CliRunner()


def _add_dependency(settings, item: str, depends_on: str) -> None:
    """Insert a dependency row directly; the lifecycle refuses cycles."""
    conn = sqlite3.connect(settings.sqlite_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO dependencies (item_id, depends_on) "
                "SELECT i.id, d.id FROM items i, items d "
                "WHERE i.project || '-' || i.number = ? AND d.project || '-' || d.number = ?",
                (item, depends_on),
            )
    finally:
        conn.close()


def test_validate_clean_with_no_depends(sqlite_env) -> None:
    sql.new("A")
    assert "OK" in sql.run("validate")


def test_validate_clean_with_valid_depends(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    sql.run("depend", b, "--on", a)
    assert "OK" in sql.run("validate")


def test_validate_simple_cycle_errors(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    sql.run("depend", a, "--on", b)
    _add_dependency(sqlite_env, b, a)
    assert "dependency_cycle" in sql.run("validate", code=1)


def test_validate_transitive_cycle_errors(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    c = sql.new("C")
    sql.run("depend", a, "--on", b)
    sql.run("depend", b, "--on", c)
    _add_dependency(sqlite_env, c, a)
    assert "dependency_cycle" in sql.run("validate", code=1)


def test_validate_json_includes_graph_findings(sqlite_env) -> None:
    a = sql.new("A")
    b = sql.new("B")
    sql.run("depend", a, "--on", b)
    _add_dependency(sqlite_env, b, a)
    data = json.loads(sql.run("validate", "--json", code=1))
    assert data["ok"] is False
    codes = {f["code"] for f in data["findings"]}
    assert "dependency_cycle" in codes


def test_validate_handles_multi_edge_graph(sqlite_env) -> None:
    """Item with multiple deps, all valid → no findings."""
    a = sql.new("A")
    b = sql.new("B")
    c = sql.new("C")
    sql.run("depend", c, "--on", a, "--on", b)
    assert sql.show(c)["frontmatter"]["depends_on"] == [a, b]
    assert "OK" in sql.run("validate")
