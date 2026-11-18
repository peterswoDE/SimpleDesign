"""Editing state of the sketch editor (no Qt, so it can be tested directly).

The session owns a working copy of a sketch plus the user's dimension
formulas. Every change re-solves the sketch; a change that makes the sketch
contradictory is rolled back and reported as :class:`SketchEditError`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from simpledesign.core.params.errors import FormulaError
from simpledesign.core.params.expression import format_number
from simpledesign.core.sketch import profile
from simpledesign.core.sketch.model import (
    P2,
    Arc,
    Circle,
    Constraint,
    ConstraintKind,
    Line,
    Sketch,
)
from simpledesign.core.sketch.solver import NumpySolver, References, SolveResult, Status

K = ConstraintKind
AXIS_SNAP_DEGREES = 3.0  # nearly horizontal/vertical lines get the constraint automatically
HISTORY = 100


# Reference points that only define the axis directions: not snapped or picked.
DIRECTION_POINTS = frozenset({"ux", "vy"})


class EditProblem(StrEnum):
    SELECTION = "selection"  # the selection does not fit the constraint
    CONTRADICTION = "contradiction"  # the new constraint contradicts the others
    REDUNDANT = "redundant"  # the new constraint is already implied
    FORMULA = "formula"  # the formula is invalid (see ``formula_error``)


class SketchEditError(Exception):
    def __init__(self, problem: EditProblem, formula_error: FormulaError | None = None) -> None:
        super().__init__(problem)
        self.problem = problem
        self.formula_error = formula_error


@dataclass(frozen=True, slots=True)
class Snap:
    """Where a click lands: a position and the point it snapped to (if any)."""

    pos: P2
    target: str | None = None


class SketchSession:
    def __init__(
        self,
        sketch: Sketch,
        formulas: Mapping[str, str],
        references: References,
        value_of: Callable[[str], float],
    ) -> None:
        self.sketch = sketch.copy()
        self.formulas: dict[str, str] = dict(formulas)
        self.references = references
        self.value_of = value_of  # formula text -> value; raises FormulaError
        self.solver = NumpySolver()
        self.selection: list[str] = []
        self.result: SolveResult = SolveResult(Status.SOLVED, {}, {})
        self.regions: tuple[profile.Region, ...] = ()
        self.profile_problem: profile.ProfileProblem | None = None
        self.formula_errors: dict[str, FormulaError] = {}
        self._undo: list[tuple[Sketch, dict[str, str]]] = []
        self._redo: list[tuple[Sketch, dict[str, str]]] = []
        self.solve()

    # ================================================================== state
    def positions(self) -> dict[str, P2]:
        """Sketch points plus the fixed reference points."""
        found = dict(self.references.points)
        found.update({p.id: p.pos for p in self.sketch.points.values()})
        return found

    def values(self) -> dict[str, float]:
        values: dict[str, float] = {}
        self.formula_errors = {}
        for c in self.sketch.dimensions():
            try:
                values[c.id] = self.value_of(self.formulas.get(c.id, ""))
            except FormulaError as error:
                self.formula_errors[c.id] = error
                values[c.id] = self.measure(c)
        return values

    def solve(self, drag: Mapping[str, P2] | None = None) -> SolveResult:
        self.result = self.solver.solve(self.sketch, self.values(), self.references, drag)
        if self.result.ok:
            self.result.apply(self.sketch)
        self._update_profile()
        return self.result

    def _update_profile(self) -> None:
        radii = {e.id: e.radius for e in self.sketch.entities.values() if isinstance(e, Circle)}
        try:
            self.regions = profile.regions(self.sketch, self.positions(), radii)
            self.profile_problem = None
        except profile.ProfileError as error:
            self.regions = ()
            self.profile_problem = error.problem

    @property
    def valid(self) -> bool:
        """Can the sketch be used (solved, closed outline, all formulas fine)?"""
        return self.result.ok and self.profile_problem is None and not self.formula_errors

    # ================================================================== history
    def checkpoint(self) -> None:
        self._undo.append((self.sketch.copy(), dict(self.formulas)))
        del self._undo[:-HISTORY]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if self._undo:
            self._redo.append((self.sketch, self.formulas))
            self.sketch, self.formulas = self._undo.pop()
            self._after_history()

    def redo(self) -> None:
        if self._redo:
            self._undo.append((self.sketch, self.formulas))
            self.sketch, self.formulas = self._redo.pop()
            self._after_history()

    def _after_history(self) -> None:
        self.selection = [s for s in self.selection if self.exists(s)]
        self.solve()

    def _rollback(self) -> None:
        self.sketch, self.formulas = self._undo.pop()
        self.solve()

    # ================================================================== lookup
    def exists(self, obj_id: str) -> bool:
        s = self.sketch
        return (
            obj_id in s.points
            or obj_id in s.entities
            or obj_id in s.constraints
            or obj_id in self.references.points
            or obj_id in self.references.lines
        )

    def is_point(self, obj_id: str) -> bool:
        return obj_id in self.sketch.points or obj_id in self.references.points

    def is_line(self, obj_id: str) -> bool:
        return isinstance(self.sketch.entities.get(obj_id), Line) or (
            obj_id in self.references.lines
        )

    def is_round(self, obj_id: str) -> bool:
        return isinstance(self.sketch.entities.get(obj_id), Circle | Arc)

    def line_points(self, line_id: str) -> tuple[str, str]:
        entity = self.sketch.entities.get(line_id)
        if isinstance(entity, Line):
            return entity.start, entity.end
        return self.references.lines[line_id]

    def center_radius(self, obj_id: str) -> tuple[P2, float]:
        entity = self.sketch.entities[obj_id]
        pos = self.positions()
        if isinstance(entity, Circle):
            return pos[entity.center], entity.radius
        assert isinstance(entity, Arc)
        c, s = pos[entity.center], pos[entity.start]
        return c, math.hypot(s[0] - c[0], s[1] - c[1])

    def nearest_point(self, pos: P2, tolerance: float, exclude: Iterable[str] = ()) -> str | None:
        excluded = set(exclude) | DIRECTION_POINTS
        best: tuple[float, str] | None = None
        for point_id, (u, v) in self.positions().items():
            if point_id in excluded:
                continue
            d = math.hypot(u - pos[0], v - pos[1])
            if d <= tolerance and (best is None or d < best[0]):
                best = (d, point_id)
        return best[1] if best else None

    def snap(self, pos: P2, tolerance: float, exclude: Iterable[str] = ()) -> Snap:
        target = self.nearest_point(pos, tolerance, exclude)
        if target is not None:
            return Snap(self.positions()[target], target)
        return Snap((round(pos[0], 1), round(pos[1], 1)))

    def distance_to(self, obj_id: str, pos: P2) -> float:
        """Distance of ``pos`` to a point, line, circle or arc."""
        positions = self.positions()
        if obj_id in positions:
            p = positions[obj_id]
            return math.hypot(p[0] - pos[0], p[1] - pos[1])
        if self.is_line(obj_id):
            a, b = (positions[p] for p in self.line_points(obj_id))
            return _segment_distance(pos, a, b)
        entity = self.sketch.entities[obj_id]
        center, radius = self.center_radius(obj_id)
        d = abs(math.hypot(pos[0] - center[0], pos[1] - center[1]) - radius)
        if isinstance(entity, Arc):
            s, e = positions[entity.start], positions[entity.end]
            t0 = math.atan2(s[1] - center[1], s[0] - center[0])
            sweep = (math.atan2(e[1] - center[1], e[0] - center[0]) - t0) % (2 * math.pi)
            t = (math.atan2(pos[1] - center[1], pos[0] - center[0]) - t0) % (2 * math.pi)
            if t > sweep:
                return min(math.dist(pos, s), math.dist(pos, e))
        return d

    def pick(self, pos: P2, tolerance: float) -> str | None:
        """Point first, then sketch geometry, then reference lines."""
        point = self.nearest_point(pos, tolerance)
        if point is not None:
            return point
        best: tuple[float, str] | None = None
        candidates = list(self.sketch.entities) + list(self.references.lines)
        for obj_id in candidates:
            d = self.distance_to(obj_id, pos)
            # reference lines are infinite axes/edges: only count them when close
            if d <= tolerance and (best is None or d < best[0]):
                best = (d, obj_id)
        return best[1] if best else None

    # ================================================================== drawing
    def _point(self, snap: Snap) -> str:
        """Point id for a snapped position (reuses sketch points, ties to references)."""
        if snap.target is not None and snap.target in self.sketch.points:
            return snap.target
        point = self.sketch.add_point(*snap.pos)
        if snap.target is not None:
            self.sketch.add_constraint(K.COINCIDENT, (point.id, snap.target))
        return point.id

    def add_line(self, a: Snap, b: Snap, construction: bool = False) -> Line | None:
        if math.dist(a.pos, b.pos) < 1e-6:
            return None
        self.checkpoint()
        start = self._point(a)
        end = self._point(b)
        line = self.sketch.add_line(start, end, construction)
        angle = math.degrees(math.atan2(b.pos[1] - a.pos[1], b.pos[0] - a.pos[0])) % 180
        if min(angle, 180 - angle) <= AXIS_SNAP_DEGREES:
            self._try_add(K.HORIZONTAL, (line.id,))
        elif abs(angle - 90) <= AXIS_SNAP_DEGREES:
            self._try_add(K.VERTICAL, (line.id,))
        self.solve()
        return line

    def add_rectangle(self, a: Snap, b: Snap) -> list[Line]:
        if abs(a.pos[0] - b.pos[0]) < 1e-6 or abs(a.pos[1] - b.pos[1]) < 1e-6:
            return []
        self.checkpoint()
        lines = self.sketch.add_rectangle(a.pos, b.pos)
        for corner, snap in ((lines[0].start, a), (lines[2].start, b)):
            if snap.target is not None:
                self._try_add(K.COINCIDENT, (corner, snap.target))
        self.solve()
        return lines

    def add_circle(self, center: Snap, radius: float, construction: bool = False) -> Circle | None:
        if radius < 1e-6:
            return None
        self.checkpoint()
        circle = self.sketch.add_circle(self._point(center), radius, construction)
        self.solve()
        return circle

    def add_arc(self, center: Snap, start: Snap, end: P2) -> Arc | None:
        """Counter-clockwise arc; ``end`` is projected onto the circle through ``start``."""
        c, s = center.pos, start.pos
        radius = math.dist(c, s)
        angle = math.atan2(end[1] - c[1], end[0] - c[0])
        e = (c[0] + radius * math.cos(angle), c[1] + radius * math.sin(angle))
        if radius < 1e-6 or math.dist(s, e) < 1e-6:
            return None
        self.checkpoint()
        arc = self.sketch.add_arc(self._point(center), self._point(start), self._point(Snap(e)))
        self.solve()
        return arc

    def _try_add(
        self, kind: ConstraintKind, refs: tuple[str, ...], data: tuple[float, ...] = ()
    ) -> Constraint | None:
        """Add a helper constraint unless it breaks the sketch (no checkpoint)."""
        constraint = self.sketch.add_constraint(kind, refs, data)
        result = self.solver.solve(self.sketch, self.values(), self.references)
        if result.status in (Status.FAILED, Status.REDUNDANT):
            del self.sketch.constraints[constraint.id]
            return None
        return constraint

    # ================================================================== editing
    def drag(self, point_id: str, pos: P2) -> bool:
        """Move a point as far as the constraints allow (no checkpoint)."""
        if point_id not in self.sketch.points:
            return False
        return self.solve({point_id: pos}).ok

    def move_entity(self, entity_id: str, delta: P2) -> bool:
        """Drag a whole line/circle/arc by moving all of its points."""
        if entity_id not in self.sketch.entities:
            return False
        pos = self.positions()
        targets = {
            p: (pos[p][0] + delta[0], pos[p][1] + delta[1])
            for p in self.sketch.point_ids_of(entity_id)
        }
        return self.solve(targets).ok

    def delete_selection(self) -> bool:
        ids = {
            s
            for s in self.selection
            if s in self.sketch.points or s in self.sketch.entities or s in self.sketch.constraints
        }
        if not ids:
            return False
        self.checkpoint()
        self.sketch.delete(ids)
        for cid in [c for c in self.formulas if c not in self.sketch.constraints]:
            del self.formulas[cid]
        self.selection = [s for s in self.selection if self.exists(s)]
        self.solve()
        return True

    def toggle_construction(self) -> bool:
        entities = [s for s in self.selection if s in self.sketch.entities]
        if not entities:
            return False
        self.checkpoint()
        for entity_id in entities:
            entity = self.sketch.entities[entity_id]
            entity.construction = not entity.construction
        self.solve()
        return True

    def set_formula(self, constraint_id: str, text: str) -> None:
        try:
            self.value_of(text)
        except FormulaError as error:
            raise SketchEditError(EditProblem.FORMULA, error) from error
        self.checkpoint()
        self.formulas[constraint_id] = text
        if not self.solve().ok:
            self._rollback()
            raise SketchEditError(EditProblem.CONTRADICTION)

    # ================================================================== constraints
    def measure(self, c: Constraint) -> float:
        """Current value of a dimension in the sketch."""
        pos = self.positions()
        refs = c.refs
        if c.kind == K.RADIUS:
            return self.center_radius(refs[0])[1]
        if c.kind == K.ANGLE:
            return abs(math.degrees(self._angle(refs[0], refs[1])))
        if len(refs) == 1:
            refs = self.line_points(refs[0])
        if c.kind == K.DISTANCE and self.is_line(refs[1]) and refs[1] not in pos:
            a, b = (pos[p] for p in self.line_points(refs[1]))
            return _line_distance(pos[refs[0]], a, b)
        p, q = pos[refs[0]], pos[refs[1]]
        if c.kind == K.DISTANCE:
            return math.dist(p, q)
        k = 0 if c.kind == K.HDISTANCE else 1
        return q[k] - p[k]

    def _angle(self, line1: str, line2: str) -> float:
        pos = self.positions()
        a1, b1 = (pos[p] for p in self.line_points(line1))
        a2, b2 = (pos[p] for p in self.line_points(line2))
        d1 = (b1[0] - a1[0], b1[1] - a1[1])
        d2 = (b2[0] - a2[0], b2[1] - a2[1])
        return math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1])

    def constrain(self, kind: ConstraintKind | None) -> list[Constraint]:
        """Apply a constraint to the selection. ``None``: a fitting dimension.

        Raises :class:`SketchEditError`; the sketch is unchanged then.
        """
        plans = self._plan(kind)
        if not plans:
            raise SketchEditError(EditProblem.SELECTION)
        self.checkpoint()
        created: list[Constraint] = []
        for plan_kind, refs, data in plans:
            c = self.sketch.add_constraint(plan_kind, refs, data)
            if c.is_dimension:
                value = self.measure(c)
                if c.kind in (K.HDISTANCE, K.VDISTANCE) and value < 0:
                    # keep the value positive: measure from the other end
                    a, b = refs if len(refs) == 2 else self.line_points(refs[0])
                    c.refs = (b, a)
                    value = self.measure(c)
                self.formulas[c.id] = format_number(round(value, 2))
            created.append(c)
        result = self.solve()
        if result.status in (Status.FAILED, Status.REDUNDANT):
            problem = (
                EditProblem.REDUNDANT
                if result.status == Status.REDUNDANT
                else EditProblem.CONTRADICTION
            )
            self._rollback()
            raise SketchEditError(problem)
        self.selection = [c.id for c in created if c.is_dimension] or self.selection
        return created

    def _plan(
        self, kind: ConstraintKind | None
    ) -> list[tuple[ConstraintKind, tuple[str, ...], tuple[float, ...]]]:
        sel = self.selection
        points = [s for s in sel if self.is_point(s)]
        lines = [s for s in sel if self.is_line(s)]
        rounds = [s for s in sel if self.is_round(s)]
        own_lines = [s for s in lines if s in self.sketch.entities]
        n = len(sel)
        if kind in (K.HORIZONTAL, K.VERTICAL):
            if own_lines and len(own_lines) == n:
                return [(kind, (line,), ()) for line in own_lines]
            return []
        if kind == K.FIX:
            pos = self.positions()
            own = [p for p in points if p in self.sketch.points]
            return [(K.FIX, (p,), pos[p]) for p in own] if own and len(own) == n else []
        if kind == K.COINCIDENT:
            if len(points) == 2 and n == 2:
                return [(K.COINCIDENT, (points[0], points[1]), ())]
            if len(points) == 1 and n == 2 and (lines or rounds):
                return [(K.ON, (points[0], (lines or rounds)[0]), ())]
            return []
        if kind in (K.PARALLEL, K.PERPENDICULAR):
            return [(kind, (lines[0], lines[1]), ())] if len(lines) == 2 and n == 2 else []
        if kind == K.EQUAL:
            if n == 2 and (len(own_lines) == 2 or len(rounds) == 2):
                return [(K.EQUAL, (sel[0], sel[1]), ())]
            return []
        if kind == K.TANGENT:
            if n == 2 and len(rounds) == 1 and len(lines) == 1:
                return [(K.TANGENT, (lines[0], rounds[0]), ())]
            if n == 2 and len(rounds) == 2:
                (c1, r1), (c2, r2) = (self.center_radius(r) for r in rounds)
                inside = math.dist(c1, c2) < max(r1, r2)
                return [(K.TANGENT, (rounds[0], rounds[1]), (-1.0 if inside else 1.0,))]
            return []
        if kind in (K.HDISTANCE, K.VDISTANCE):
            if n == 1 and own_lines:
                return [(kind, (own_lines[0],), ())]
            if n == 2 and len(points) == 2:
                return [(kind, (points[0], points[1]), ())]
            return []
        if kind == K.RADIUS:
            return [(K.RADIUS, (r,), ()) for r in rounds] if rounds and len(rounds) == n else []
        if kind == K.ANGLE or kind is None:
            if n == 2 and len(lines) == 2:
                sign = 1.0 if self._angle(lines[0], lines[1]) >= 0 else -1.0
                return [(K.ANGLE, (lines[0], lines[1]), (sign,))]
            if kind == K.ANGLE:
                return []
        if kind in (K.DISTANCE, None):
            if n == 1 and own_lines:
                return [(K.DISTANCE, (own_lines[0],), ())]
            if n == 1 and rounds:
                return [(K.RADIUS, (rounds[0],), ())]
            if n == 2 and len(points) == 2:
                return [(K.DISTANCE, (points[0], points[1]), ())]
            if n == 2 and len(points) == 1 and len(lines) == 1:
                return [(K.DISTANCE, (points[0], lines[0]), ())]
        return []


def _segment_distance(p: P2, a: P2, b: P2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def _line_distance(p: P2, a: P2, b: P2) -> float:
    length = math.dist(a, b)
    if length < 1e-12:
        return math.dist(p, a)
    return abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / length
