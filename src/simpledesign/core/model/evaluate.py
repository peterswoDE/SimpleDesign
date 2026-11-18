"""Evaluate all formulas of a document in dependency order.

Error behaviour (plan section 3): a formula that fails (cycle, division by
zero, size <= 0 …) gets an error, and its value falls back to the *last valid*
value. So the model never ends up in a broken state.

Evaluating everything is cheap (pure Python, microseconds per formula);
expensive work - geometry - is cached separately per board.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from simpledesign.core.model.document import Document, node_key, ref_key
from simpledesign.core.model.machining import Machining, SketchFeature, compute_machining
from simpledesign.core.model.part import (
    DIMENSIONS,
    EDITABLE_PROPERTIES,
    END_PROPERTY,
    Axis,
    Board,
    Face,
    Orientation,
    default_expressions,
    is_sketch_property,
)
from simpledesign.core.model.state import BoardState
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import Node, evaluate, parse, references
from simpledesign.core.params.graph import topological_order
from simpledesign.core.sketch import profile
from simpledesign.core.sketch.model import ConstraintKind, Sketch
from simpledesign.core.sketch.solver import NumpySolver, References, SolveResult


@dataclass(slots=True)
class SketchSolution:
    """Solved sketch of a board (profile or cut-out)."""

    result: SolveResult
    regions: tuple[profile.Region, ...] = ()  # last valid regions (may be empty)
    error: FormulaError | None = None
    # Profiles: min corner of the bounding box (the part's local origin).
    offset: tuple[float, float] = (0.0, 0.0)


@dataclass(slots=True)
class Evaluation:
    values: dict[str, float]
    errors: dict[str, FormulaError]
    boards: dict[str, BoardState]
    machining: Machining
    # Key: board id (profile) or "<board id>/<cut id>".
    sketches: dict[str, SketchSolution] = field(default_factory=dict)

    def value(self, owner_id: str, prop: str | None = None) -> float:
        return self.values[node_key(owner_id, prop)]

    def error(self, owner_id: str, prop: str | None = None) -> FormulaError | None:
        return self.errors.get(node_key(owner_id, prop))


_DEFAULTS = {prop: float(text) for prop, text in default_expressions().items()}


def _validate(prop: str | None, value: float, signed: bool = False) -> float:
    sketch_value = bool(prop) and is_sketch_property(prop or "") and not signed
    if (prop in DIMENSIONS or sketch_value) and value <= 0:
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
        self.last_regions: dict[str, tuple[profile.Region, ...]] = {}
        self.solver = NumpySolver()

    def evaluate(self, doc: Document) -> Evaluation:
        parsed: dict[str, Node] = {}
        prop_of: dict[str, str | None] = {}
        derived: dict[str, Callable[[dict[str, float]], float]] = {}
        sketches: dict[str, SketchSolution] = {}
        signed = {
            node_key(board.id, c.formula_key)
            for board in doc.boards.values()
            for _key, sketch in board.sketches()
            for c in sketch.constraints.values()
            if c.kind == ConstraintKind.ANGLE
        }
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
                derived[key] = _sum(start, size)
            if board.profile is not None:
                self._profile_nodes(board, board.profile, prop_of, deps, derived, sketches)

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
                    value = derived[key](values)
                else:
                    value = evaluate(parsed[key], lambda ref: values[ref_key(ref)])
                    value = _validate(prop_of[key], value, key in signed)
            except FormulaError as error:
                errors[key] = error
                values[key] = fallback(key)
            else:
                values[key] = value
                self.last_valid[key] = value
        for key in blocked:
            values[key] = fallback(key)

        boards = {
            board.id: _board_state(
                board.id, board.orientation, board.pattern_axis, values, board.rotation_axis
            )
            for board in doc.boards.values()
        }
        for board in doc.boards.values():
            if board.profile is not None and board.id not in sketches:
                # length/width failed before solving (e.g. a dimension error)
                self._profile_solution(board, board.profile, values, sketches)
        features: dict[str, list[SketchFeature]] = {}
        for board in doc.boards.values():
            state = boards[board.id]
            if board.id in sketches and sketches[board.id].regions:
                features.setdefault(board.id, []).append(
                    SketchFeature(sketches[board.id].regions, cut=False)
                )
            for cut in board.cuts:
                key = f"{board.id}/{cut.id}"
                refs = References.for_face(state.length, state.width)
                sketches[key] = solution = self._solve(key, board, cut.sketch, refs, values)
                if solution.regions:
                    depth = values.get(node_key(board.id, cut.depth_key), state.thickness)
                    features.setdefault(board.id, []).append(
                        SketchFeature(
                            solution.regions,
                            top=cut.face == Face.TOP,
                            depth=None if cut.through else depth,
                        )
                    )
        machining = compute_machining(doc, boards, features)
        return Evaluation(values, errors, boards, machining, sketches)

    # ------------------------------------------------------------------ sketches
    def _solve(
        self,
        key: str,
        board: Board,
        sketch: Sketch,
        references: References,
        values: dict[str, float],
    ) -> SketchSolution:
        dims = {
            c.id: values.get(node_key(board.id, c.formula_key), 0.0) for c in sketch.dimensions()
        }
        result = self.solver.solve(sketch, dims, references)
        error: FormulaError | None = None
        if not result.ok:
            error = FormulaError(ErrorCode.SKETCH_UNSOLVABLE, constraints=result.failed)
        else:
            try:
                self.last_regions[key] = profile.regions(sketch, result.points, result.radii)
            except profile.ProfileError as problem:
                error = FormulaError(ErrorCode.PROFILE_INVALID, problem=problem.problem.value)
        return SketchSolution(result, self.last_regions.get(key, ()), error)

    def _profile_solution(
        self,
        board: Board,
        sketch: Sketch,
        values: dict[str, float],
        sketches: dict[str, SketchSolution],
    ) -> SketchSolution:
        if board.id not in sketches:
            found = self._solve(board.id, board, sketch, References.for_plane(), values)
            if found.regions:
                lo, _hi = profile.bounds(found.regions)
                found.regions = profile.translate(found.regions, -lo[0], -lo[1])
                found.offset = lo
            sketches[board.id] = found
        return sketches[board.id]

    def _profile_nodes(
        self,
        board: Board,
        sketch: Sketch,
        prop_of: dict[str, str | None],
        deps: dict[str, set[str]],
        derived: dict[str, Callable[[dict[str, float]], float]],
        sketches: dict[str, SketchSolution],
    ) -> None:
        """Length and width of a profile part follow from its solved sketch."""
        dim_keys = {node_key(board.id, c.formula_key) for c in sketch.dimensions()}

        def size(axis: int) -> Callable[[dict[str, float]], float]:
            def compute(values: dict[str, float]) -> float:
                found = self._profile_solution(board, sketch, values, sketches)
                if found.error is not None:
                    raise found.error
                lo, hi = profile.bounds(found.regions)
                return hi[axis] - lo[axis]

            return compute

        for axis, prop in enumerate(("length", "width")):
            key = node_key(board.id, prop)
            prop_of[key] = prop
            deps[key] = set(dim_keys)
            derived[key] = size(axis)


def _sum(a: str, b: str) -> Callable[[dict[str, float]], float]:
    return lambda values: values[a] + values[b]


def _board_state(
    board_id: str,
    orientation: Orientation,
    pattern_axis: Axis,
    values: dict[str, float],
    rotation_axis: Axis = Axis.Z,
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
        angle=v["angle"],
        rotation_axis=rotation_axis,
    )
