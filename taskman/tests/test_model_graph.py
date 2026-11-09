"""Tests for taskman.model.graph: cycle detection and unknown-reference checks."""
from __future__ import annotations

from taskman.model.graph import find_cycles, unknown_references


def test_unknown_references_empty_graph() -> None:
    assert unknown_references({}) == []


def test_unknown_references_clean() -> None:
    g = {"a": ["b"], "b": ["c"], "c": []}
    assert unknown_references(g) == []


def test_unknown_references_finds_missing() -> None:
    g = {"a": ["b", "x"], "b": ["y"], "c": []}
    refs = unknown_references(g)
    assert sorted(refs) == [("a", "x"), ("b", "y")]


def test_find_cycles_empty_graph() -> None:
    assert find_cycles({}) == []


def test_find_cycles_acyclic() -> None:
    g = {"a": ["b"], "b": ["c"], "c": []}
    assert find_cycles(g) == []


def test_find_cycles_self_loop() -> None:
    g = {"a": ["a"]}
    cycles = find_cycles(g)
    assert cycles == [["a", "a"]]


def test_find_cycles_simple_two_node() -> None:
    g = {"a": ["b"], "b": ["a"]}
    cycles = find_cycles(g)
    assert len(cycles) == 1
    # Cycle in some rotation: [a, b, a] or [b, a, b]
    c = cycles[0]
    assert len(c) == 3
    assert c[0] == c[-1]


def test_find_cycles_transitive() -> None:
    g = {"a": ["b"], "b": ["c"], "c": ["a"]}
    cycles = find_cycles(g)
    assert len(cycles) == 1
    c = cycles[0]
    assert c[0] == c[-1]
    assert len(c) == 4  # a -> b -> c -> a


def test_find_cycles_ignores_unknown_refs() -> None:
    g = {"a": ["b", "x"]}  # x not in graph
    assert find_cycles(g) == []


def test_find_cycles_independent_cycles() -> None:
    g = {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}
    cycles = find_cycles(g)
    assert len(cycles) == 2
