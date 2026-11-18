"""Evaluate all formulas of a document in dependency order.

Error behaviour (plan section 3): a formula that fails (cycle, division by
zero, size <= 0 …) gets an error, and its value falls back to the *last valid*
value. So the model never ends up in a broken state.

Evaluating everything is cheap (pure Python, microseconds per formula);
expensive work - geometry - is cached separately per board.
"""

from __future__ import annotations

from dataclasses import dataclass

from simpledesign.core.model.document import Document, node_key, ref_key
from simpledesign.core.model.part import (
    DIMENSIONS,
    EDITABLE_PROPERTIES,
    END_PROPERTY,
    Axis,
    Orientation,
    default_expressions,
)
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import Node, evaluate, parse, references
from simpledesign.core.params.graph import topological_order

type Vec3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class BoardState:
    """Computed values of one board."""

    length: float
    width: float
    thickness: float
    x: float
    y: float
    z: float
    count: int
    spacing: float
    orientation: Orientation
    pattern_axis: Axis

    def extent(self, axis: Axis) -> float:
        return float(getattr(self, self.orientation.dimension_along(axis)))

    @property
    def size(self) -> Vec3:
        """World-axis-aligned size (sx, sy, sz)."""
        return (self.extent(Axis.X), self.extent(Axis.Y), self.extent(Axis.Z))

    @property
    def origin(self) -> Vec3:
        return (self.x, self.y, self.z)

    def instance_origins(self) -> list[Vec3]:
        """Min corner of every copy of the pattern (first = the board itself)."""
        origins = []
        for i in range(self.count):
            point = [self.x, self.y, self.z]
            point[self.pattern_axis.position] += i * self.spacing
            origins.append((point[0], point[1], point[2]))
        return origins


@dataclass(slots=True)
class Evaluation:
    values: dict[str, float]
    errors: dict[str, FormulaError]
    boards: dict[str, BoardState]

    def value(self, owner_id: str, prop: str | None = None) -> float:
        return self.values[node_key(owner_id, prop)]

    def error(self, owner_id: str, prop: str | None = None) -> FormulaError | None:
        return self.errors.get(node_key(owner_id, prop))


_DEFAULTS = {prop: float(text) for prop, text in default_expressions().items()}


def _validate(prop: str | None, value: float) -> float:
    if prop in DIMENSIONS and value <= 0:
        raise FormulaError(ErrorCode.NOT_POSITIVE, value=value)
    if prop == "count":
        if abs(value - round(value)) > 1e-9:
            raise FormulaError(ErrorCode.NOT_INTEGER, value=value)
        if round(value) < 1:
            raise FormulaError(ErrorCode.NOT_POSITIVE, value=value)
        return float(round(value))
    return value


class Evaluator:
    """Keeps the last valid value of every formula between evaluations."""

    def __init__(self) -> None:
        self.last_valid: dict[str, float] = {}

    def evaluate(self, doc: Document) -> Evaluation:
        parsed: dict[str, Node] = {}
        prop_of: dict[str, str | None] = {}
        derived: dict[str, tuple[str, str]] = {}
        deps: dict[str, set[str]] = {}
        errors: dict[str, FormulaError] = {}

        for owner_id, prop in doc.slots():
            key = node_key(owner_id, prop)
            prop_of[key] = prop
            try:
                node = parse(doc.stored_expression(owner_id, prop))
            except FormulaError as error:
                errors[key] = error
                deps[key] = set()
                continue
            parsed[key] = node
            deps[key] = {ref_key(ref) for ref in references(node)}

        # Derived end coordinates: x2 = x + size along X, …
        for board in doc.boards.values():
            for axis, end_prop in END_PROPERTY.items():
                key = node_key(board.id, end_prop)
                start = node_key(board.id, axis.value)
                size = node_key(board.id, board.orientation.dimension_along(axis))
                prop_of[key] = end_prop
                deps[key] = {start, size}
                derived[key] = (start, size)

        for key, key_deps in deps.items():
            unknown = sorted(d for d in key_deps if d not in deps)
            if unknown and key not in errors:
                errors[key] = FormulaError(ErrorCode.UNKNOWN_NAME, name=unknown[0])

        order, blocked = topological_order(deps)
        for key in blocked:
            errors.setdefault(key, FormulaError(ErrorCode.CYCLE))

        values: dict[str, float] = {}

        def fallback(key: str) -> float:
            prop = prop_of[key]
            return self.last_valid.get(key, _DEFAULTS.get(prop or "", 0.0))

        for key in order:
            if key in errors:
                values[key] = fallback(key)
                continue
            try:
                if key in derived:
                    start_key, size_key = derived[key]
                    value = values[start_key] + values[size_key]
                else:
                    value = evaluate(parsed[key], lambda ref: values[ref_key(ref)])
                    value = _validate(prop_of[key], value)
            except FormulaError as error:
                errors[key] = error
                values[key] = fallback(key)
            else:
                values[key] = value
                self.last_valid[key] = value
        for key in blocked:
            values[key] = fallback(key)

        boards = {
            board.id: _board_state(board.id, board.orientation, board.pattern_axis, values)
            for board in doc.boards.values()
        }
        return Evaluation(values, errors, boards)


def _board_state(
    board_id: str, orientation: Orientation, pattern_axis: Axis, values: dict[str, float]
) -> BoardState:
    v = {prop: values[node_key(board_id, prop)] for prop in EDITABLE_PROPERTIES}
    # A board with a still-invalid size (never valid) must not reach the geometry kernel.
    for prop in DIMENSIONS:
        if v[prop] <= 0:
            v[prop] = _DEFAULTS[prop]
    return BoardState(
        length=v["length"],
        width=v["width"],
        thickness=v["thickness"],
        x=v["x"],
        y=v["y"],
        z=v["z"],
        count=max(1, round(v["count"])),
        spacing=v["spacing"],
        orientation=orientation,
        pattern_axis=pattern_axis,
    )
