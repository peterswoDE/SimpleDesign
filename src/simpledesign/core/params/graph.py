"""Dependency graph: evaluation order and cycle detection."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping


def topological_order(deps: Mapping[str, Iterable[str]]) -> tuple[list[str], set[str]]:
    """Order nodes so that every node comes after its dependencies (Kahn's algorithm).

    ``deps[node]`` lists the nodes ``node`` depends on. Dependencies that are not
    keys of ``deps`` are ignored (the caller reports them as unknown).

    Returns ``(order, blocked)``: ``blocked`` holds every node that is part of a
    cycle or depends on one - those cannot be computed.
    """
    dependents: dict[str, list[str]] = {node: [] for node in deps}
    missing: dict[str, int] = {}
    for node, node_deps in deps.items():
        known = {d for d in node_deps if d in deps}
        missing[node] = len(known)
        for dep in known:
            dependents[dep].append(node)

    queue = deque(node for node, count in missing.items() if count == 0)
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for dependent in dependents[node]:
            missing[dependent] -= 1
            if missing[dependent] == 0:
                queue.append(dependent)

    blocked = set(deps) - set(order)
    return order, blocked


def transitive_dependents(deps: Mapping[str, Iterable[str]], targets: Iterable[str]) -> set[str]:
    """All nodes that (directly or indirectly) depend on one of ``targets``."""
    dependents: dict[str, set[str]] = {}
    for node, node_deps in deps.items():
        for dep in node_deps:
            dependents.setdefault(dep, set()).add(node)
    result: set[str] = set()
    stack = list(targets)
    while stack:
        for dependent in dependents.get(stack.pop(), ()):
            if dependent not in result:
                result.add(dependent)
                stack.append(dependent)
    return result
