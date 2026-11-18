from __future__ import annotations

from simpledesign.core.params.graph import topological_order, transitive_dependents


def test_order_respects_dependencies() -> None:
    deps = {"c": ["a", "b"], "b": ["a"], "a": []}
    order, blocked = topological_order(deps)
    assert order.index("a") < order.index("b") < order.index("c")
    assert blocked == set()


def test_cycle_blocks_members_and_dependents_only() -> None:
    deps = {"a": ["b"], "b": ["a"], "c": ["a"], "d": []}
    order, blocked = topological_order(deps)
    assert order == ["d"]
    assert blocked == {"a", "b", "c"}


def test_self_reference_is_a_cycle() -> None:
    _, blocked = topological_order({"a": ["a"]})
    assert blocked == {"a"}


def test_unknown_dependencies_are_ignored() -> None:
    order, blocked = topological_order({"a": ["missing"]})
    assert order == ["a"]
    assert not blocked


def test_transitive_dependents() -> None:
    deps = {"b": ["a"], "c": ["b"], "d": ["x"]}
    assert transitive_dependents(deps, ["a"]) == {"b", "c"}
