"""Dependency graph helpers: cycle detection and unknown-reference validation.

A graph here is a ``dict[str, list[str]]`` mapping item IDs to the list of
IDs they depend on. Used by ``validate`` (cycles + unknown refs) and by the
upcoming ``ready`` / ``dependents`` / ``waiting-on`` query commands.
"""
from __future__ import annotations


def unknown_references(graph: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Return ``(source_id, missing_dep_id)`` for every reference to an unknown ID."""
    known = set(graph)
    out: list[tuple[str, str]] = []
    for source, deps in graph.items():
        for dep in deps:
            if dep not in known:
                out.append((source, dep))
    return out


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Return all simple cycles in the graph.

    Each cycle is a list of IDs in traversal order, with the first node
    repeated at the end (``[a, b, a]``). Independent cycles each appear once;
    cycles overlapping in nodes are reported once per discovery edge.

    Unknown references (deps to IDs not in the graph) are silently ignored
    by cycle search — :func:`unknown_references` reports them separately.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        path.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue  # unknown reference; reported by unknown_references()
            if color[dep] == GRAY:
                start = path.index(dep)
                cycles.append(path[start:] + [dep])
            elif color[dep] == WHITE:
                dfs(dep, path)
        color[node] = BLACK
        path.pop()

    # Sort node list for deterministic traversal order.
    for node in sorted(graph):
        if color[node] == WHITE:
            dfs(node, [])
    return cycles
