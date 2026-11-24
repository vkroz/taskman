"""Integrity checks of the dependency graph."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from taskman.model.graph import find_cycles, unknown_references

if TYPE_CHECKING:
    from taskman.storage.store import Item, Store


@dataclass(frozen=True, slots=True)
class Finding:
    """A single validation finding."""

    path: str
    severity: str  # "error" | "warning"
    code: str
    message: str


def check_dependencies(items: list[Item]) -> list[Finding]:
    """Unknown dependency IDs (warnings) and dependency cycles (errors)."""
    readable = sorted((item for item in items if not item.error), key=lambda item: item.location)
    graph = {item.id: list(item.depends_on) for item in readable}
    locations = {item.id: item.location for item in readable}
    findings = [
        Finding(
            locations.get(source_id, source_id),
            "warning",
            "unknown_dependency",
            f"depends_on references unknown ID: {missing}",
        )
        for source_id, missing in unknown_references(graph)
    ]
    for cycle in find_cycles(graph):
        findings.append(
            Finding(
                locations.get(cycle[0], cycle[0]),
                "error",
                "dependency_cycle",
                f"dependency cycle: {' -> '.join(cycle)}",
            )
        )
    return findings


def check(store: Store) -> list[Finding]:
    """Unknown dependencies and dependency cycles of all items."""
    return check_dependencies(store.items())
