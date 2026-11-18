"""Own geometric constraint solver (numpy, no external CAD kernel).

Unknowns are the coordinates of all sketch points and the circle radii.
Each constraint contributes one or more residuals that are zero when it is
satisfied. A damped Gauss-Newton (Levenberg-Marquardt) iteration with the
minimum-norm step ``dx = -J^T (J J^T + lambda I)^-1 r`` moves the geometry as
little as possible, so under-determined sketches keep their drawn shape.

The degrees of freedom are ``n - rank(J)`` at the solution; ``rank(J) < m``
means some constraints are redundant.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import numpy as np

from simpledesign.core.sketch.model import (
    ORIGIN,
    P2,
    Arc,
    Circle,
    Constraint,
    ConstraintKind,
    Line,
    Sketch,
)

TOLERANCE = 1e-7  # mm
_EPS = 1e-12


# ====================================================================== references
@dataclass(frozen=True, slots=True)
class References:
    """Fixed geometry the user can constrain against: points and lines."""

    points: Mapping[str, P2]
    lines: Mapping[str, tuple[str, str]]

    @staticmethod
    def for_plane() -> References:
        """Origin ``o`` and the axes ``ox`` / ``oy``."""
        return References(
            points={ORIGIN: (0.0, 0.0), "ux": (100.0, 0.0), "vy": (0.0, 100.0)},
            lines={"ox": (ORIGIN, "ux"), "oy": (ORIGIN, "vy")},
        )

    @staticmethod
    def for_face(length: float, width: float) -> References:
        """The face rectangle ``0..length x 0..width`` plus origin and axes."""
        base = References.for_plane()
        points = dict(base.points)
        points.update({"r1": (length, 0.0), "r2": (length, width), "r3": (0.0, width)})
        corners = (ORIGIN, "r1", "r2", "r3")
        lines = dict(base.lines)
        lines.update({f"e{i}": (corners[i], corners[(i + 1) % 4]) for i in range(4)})
        return References(points=points, lines=lines)


# ====================================================================== result
class Status(StrEnum):
    SOLVED = "solved"  # fully determined
    UNDERDETERMINED = "underdetermined"  # solved, free degrees of freedom left
    REDUNDANT = "redundant"  # solved, but some constraints are redundant
    FAILED = "failed"  # contradicting constraints / did not converge


@dataclass(slots=True)
class SolveResult:
    status: Status
    points: dict[str, P2]
    radii: dict[str, float]
    dof: int = 0
    # Constraints whose residual is not satisfied (FAILED only).
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != Status.FAILED

    def apply(self, sketch: Sketch) -> None:
        """Store the solution as the sketch's last valid state."""
        for point_id, (u, v) in self.points.items():
            if point_id in sketch.points:
                sketch.points[point_id].u = u
                sketch.points[point_id].v = v
        for circle_id, radius in self.radii.items():
            entity = sketch.entities.get(circle_id)
            if isinstance(entity, Circle):
                entity.radius = radius


class SketchSolver(Protocol):
    def solve(
        self,
        sketch: Sketch,
        values: Mapping[str, float],
        references: References,
        drag: Mapping[str, P2] | None = None,
    ) -> SolveResult: ...


# ====================================================================== system
type Vec = np.ndarray
type Fn = Callable[[Vec], list[float]]


@dataclass(slots=True)
class _Block:
    owner: str  # constraint id ("" for internal arc equations)
    vars: list[int]
    fn: Fn


class _System:
    def __init__(self, sketch: Sketch, references: References) -> None:
        self.sketch = sketch
        self.refs = references
        self.index: dict[str, int] = {}  # point id -> index of u (v = +1)
        self.radius_index: dict[str, int] = {}
        x: list[float] = []
        for point in sketch.points.values():
            if point.id in references.points:
                continue
            self.index[point.id] = len(x)
            x += [point.u, point.v]
        for entity in sketch.entities.values():
            if isinstance(entity, Circle):
                self.radius_index[entity.id] = len(x)
                x.append(entity.radius)
        self.x0 = np.array(x, dtype=float)
        self.blocks: list[_Block] = []

    # -------------------------------------------------------------- accessors
    def point_vars(self, point_id: str) -> list[int]:
        if point_id in self.index:
            i = self.index[point_id]
            return [i, i + 1]
        if point_id in self.refs.points:
            return []
        raise KeyError(point_id)

    def point(self, point_id: str) -> Callable[[Vec], P2]:
        if point_id in self.index:
            i = self.index[point_id]
            return lambda x: (x[i], x[i + 1])
        fixed = self.refs.points[point_id]
        return lambda _x: fixed

    def line(self, line_id: str) -> tuple[str, str]:
        entity = self.sketch.entities.get(line_id)
        if isinstance(entity, Line):
            return (entity.start, entity.end)
        if line_id in self.refs.lines:
            return self.refs.lines[line_id]
        raise KeyError(line_id)

    def is_line(self, obj_id: str) -> bool:
        return isinstance(self.sketch.entities.get(obj_id), Line) or obj_id in self.refs.lines

    def is_round(self, obj_id: str) -> bool:
        return isinstance(self.sketch.entities.get(obj_id), Circle | Arc)

    def center_radius(
        self, obj_id: str
    ) -> tuple[Callable[[Vec], P2], Callable[[Vec], float], list[int]]:
        entity = self.sketch.entities[obj_id]
        if isinstance(entity, Circle):
            r = self.radius_index[entity.id]
            return (
                self.point(entity.center),
                (lambda x: float(x[r])),
                [*self.point_vars(entity.center), r],
            )
        if isinstance(entity, Arc):
            c, s = self.point(entity.center), self.point(entity.start)
            return (
                c,
                lambda x: _dist(c(x), s(x)),
                self.point_vars(entity.center) + self.point_vars(entity.start),
            )
        raise KeyError(obj_id)

    def add(self, owner: str, variables: list[int], fn: Fn) -> None:
        self.blocks.append(_Block(owner, sorted(set(variables)), fn))

    # -------------------------------------------------------------- evaluation
    def residual(self, x: Vec) -> Vec:
        out: list[float] = []
        for block in self.blocks:
            out += block.fn(x)
        return np.array(out, dtype=float)

    def jacobian(self, x: Vec) -> Vec:
        rows: list[Vec] = []
        h = 1e-6
        for block in self.blocks:
            base = block.fn(x)
            part = np.zeros((len(base), len(x)))
            for j in block.vars:
                xp = x.copy()
                xm = x.copy()
                xp[j] += h
                xm[j] -= h
                part[:, j] = (np.array(block.fn(xp)) - np.array(block.fn(xm))) / (2 * h)
            rows.append(part)
        if not rows:
            return np.zeros((0, len(x)))
        return np.vstack(rows)


# ====================================================================== helpers
def _dist(a: P2, b: P2) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _unit(a: P2, b: P2) -> P2:
    d = _dist(a, b)
    if d < _EPS:
        return (0.0, 0.0)
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _cross(a: P2, b: P2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _dot(a: P2, b: P2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _point_line_distance(p: P2, a: P2, b: P2) -> float:
    """Signed distance of ``p`` from the line through ``a`` and ``b``."""
    d = _unit(a, b)
    return _cross(d, (p[0] - a[0], p[1] - a[1]))


# ====================================================================== constraints
def _add_constraint(system: _System, c: Constraint, values: Mapping[str, float]) -> None:
    kind, refs = c.kind, c.refs
    s = system
    value = values.get(c.id, 0.0)

    def pv(*point_ids: str) -> list[int]:
        return [i for p in point_ids for i in s.point_vars(p)]

    if kind == ConstraintKind.COINCIDENT:
        p, q = s.point(refs[0]), s.point(refs[1])
        s.add(c.id, pv(*refs), lambda x: [p(x)[0] - q(x)[0], p(x)[1] - q(x)[1]])
    elif kind == ConstraintKind.FIX:
        p = s.point(refs[0])
        fu, fv = c.data
        s.add(c.id, pv(refs[0]), lambda x: [p(x)[0] - fu, p(x)[1] - fv])
    elif kind in (ConstraintKind.HORIZONTAL, ConstraintKind.VERTICAL):
        a_id, b_id = s.line(refs[0])
        a, b = s.point(a_id), s.point(b_id)
        k = 1 if kind == ConstraintKind.HORIZONTAL else 0
        s.add(c.id, pv(a_id, b_id), lambda x: [b(x)[k] - a(x)[k]])
    elif kind == ConstraintKind.ON:
        p = s.point(refs[0])
        if s.is_line(refs[1]):
            a_id, b_id = s.line(refs[1])
            a, b = s.point(a_id), s.point(b_id)
            s.add(
                c.id,
                pv(refs[0], a_id, b_id),
                lambda x: [_point_line_distance(p(x), a(x), b(x))],
            )
        else:
            center, radius, rv = s.center_radius(refs[1])
            s.add(c.id, pv(refs[0]) + rv, lambda x: [_dist(center(x), p(x)) - radius(x)])
    elif kind in (ConstraintKind.PARALLEL, ConstraintKind.PERPENDICULAR, ConstraintKind.ANGLE):
        a1, b1 = s.line(refs[0])
        a2, b2 = s.line(refs[1])
        pa1, pb1, pa2, pb2 = s.point(a1), s.point(b1), s.point(a2), s.point(b2)
        variables = pv(a1, b1, a2, b2)
        if kind == ConstraintKind.PARALLEL:
            s.add(
                c.id,
                variables,
                lambda x: [_cross(_unit(pa1(x), pb1(x)), _unit(pa2(x), pb2(x)))],
            )
        elif kind == ConstraintKind.PERPENDICULAR:
            s.add(c.id, variables, lambda x: [_dot(_unit(pa1(x), pb1(x)), _unit(pa2(x), pb2(x)))])
        else:
            sign = c.data[0] if c.data else 1.0
            t = math.radians(value) * sign
            cos_t, sin_t = math.cos(t), math.sin(t)

            def angle(x: Vec) -> list[float]:
                d1, d2 = _unit(pa1(x), pb1(x)), _unit(pa2(x), pb2(x))
                # sin(theta - t), theta = angle from d1 to d2
                return [_cross(d1, d2) * cos_t - _dot(d1, d2) * sin_t]

            s.add(c.id, variables, angle)
    elif kind == ConstraintKind.TANGENT:
        first, second = refs
        if s.is_line(first) or s.is_line(second):
            line_id, round_id = (first, second) if s.is_line(first) else (second, first)
            a_id, b_id = s.line(line_id)
            a, b = s.point(a_id), s.point(b_id)
            center, radius, rv = s.center_radius(round_id)
            s.add(
                c.id,
                pv(a_id, b_id) + rv,
                lambda x: [abs(_point_line_distance(center(x), a(x), b(x))) - radius(x)],
            )
        else:
            c1, r1, v1 = s.center_radius(first)
            c2, r2, v2 = s.center_radius(second)
            inside = bool(c.data) and c.data[0] < 0
            if inside:
                s.add(c.id, v1 + v2, lambda x: [_dist(c1(x), c2(x)) - abs(r1(x) - r2(x))])
            else:
                s.add(c.id, v1 + v2, lambda x: [_dist(c1(x), c2(x)) - r1(x) - r2(x)])
    elif kind == ConstraintKind.EQUAL:
        if s.is_line(refs[0]):
            a1, b1 = s.line(refs[0])
            a2, b2 = s.line(refs[1])
            pa1, pb1, pa2, pb2 = s.point(a1), s.point(b1), s.point(a2), s.point(b2)
            s.add(
                c.id,
                pv(a1, b1, a2, b2),
                lambda x: [_dist(pa1(x), pb1(x)) - _dist(pa2(x), pb2(x))],
            )
        else:
            _c1, r1, v1 = s.center_radius(refs[0])
            _c2, r2, v2 = s.center_radius(refs[1])
            s.add(c.id, v1 + v2, lambda x: [r1(x) - r2(x)])
    elif kind == ConstraintKind.RADIUS:
        _c, radius, rv = s.center_radius(refs[0])
        s.add(c.id, rv, lambda x: [radius(x) - value])
    elif kind in (ConstraintKind.DISTANCE, ConstraintKind.HDISTANCE, ConstraintKind.VDISTANCE):
        if len(refs) == 1:  # a line: its length
            refs = s.line(refs[0])
        if kind == ConstraintKind.DISTANCE and s.is_line(refs[1]) and refs[1] not in s.refs.points:
            # point - line distance
            p = s.point(refs[0])
            a_id, b_id = s.line(refs[1])
            a, b = s.point(a_id), s.point(b_id)
            s.add(
                c.id,
                pv(refs[0], a_id, b_id),
                lambda x: [abs(_point_line_distance(p(x), a(x), b(x))) - value],
            )
            return
        p, q = s.point(refs[0]), s.point(refs[1])
        if kind == ConstraintKind.DISTANCE:
            s.add(c.id, pv(*refs), lambda x: [_dist(p(x), q(x)) - value])
        else:
            k = 0 if kind == ConstraintKind.HDISTANCE else 1
            s.add(c.id, pv(*refs), lambda x: [q(x)[k] - p(x)[k] - value])
    else:  # pragma: no cover - all kinds handled
        raise ValueError(kind)


type PointFn = Callable[[Vec], P2]


def _equal_radius(c: PointFn, s: PointFn, e: PointFn) -> Fn:
    return lambda x: [_dist(c(x), s(x)) - _dist(c(x), e(x))]


def _at(p: PointFn, target: P2) -> Fn:
    return lambda x: [p(x)[0] - target[0], p(x)[1] - target[1]]


def _build(sketch: Sketch, values: Mapping[str, float], references: References) -> _System:
    system = _System(sketch, references)
    for entity in sketch.entities.values():
        if isinstance(entity, Arc):  # start and end on the same circle
            c, s, e = (system.point(p) for p in (entity.center, entity.start, entity.end))
            variables = [
                i for p in (entity.center, entity.start, entity.end) for i in system.point_vars(p)
            ]
            system.add("", variables, _equal_radius(c, s, e))
    for constraint in sketch.constraints.values():
        _add_constraint(system, constraint, values)
    return system


# ====================================================================== solving
def _iterate(system: _System, x: Vec, max_iterations: int = 200) -> tuple[Vec, Vec]:
    r = system.residual(x)
    lam = 1e-6
    for _ in range(max_iterations):
        if r.size == 0 or float(np.max(np.abs(r))) < TOLERANCE * 0.01:
            break
        jac = system.jacobian(x)
        cost = float(r @ r)
        improved = False
        for _try in range(12):
            a = jac @ jac.T + lam * np.eye(len(r))
            try:
                y = np.linalg.solve(a, r)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            step = -(jac.T @ y)
            candidate = x + step
            r_new = system.residual(candidate)
            if float(r_new @ r_new) < cost:
                x, r = candidate, r_new
                lam = max(lam / 10, 1e-12)
                improved = True
                break
            lam *= 10
        if not improved:
            break
    return x, r


def _result(system: _System, x: Vec) -> tuple[dict[str, P2], dict[str, float]]:
    points = {pid: (float(x[i]), float(x[i + 1])) for pid, i in system.index.items()}
    radii = {cid: float(x[i]) for cid, i in system.radius_index.items()}
    return points, radii


class NumpySolver:
    """Default :class:`SketchSolver`."""

    def solve(
        self,
        sketch: Sketch,
        values: Mapping[str, float],
        references: References,
        drag: Mapping[str, P2] | None = None,
    ) -> SolveResult:
        system = _build(sketch, values, references)
        x0 = system.x0.copy()
        if drag:
            dragged = _build(sketch, values, references)
            for point_id, (tu, tv) in drag.items():
                if point_id not in dragged.index:
                    continue
                p = dragged.point(point_id)
                dragged.add("", dragged.point_vars(point_id), _at(p, (tu, tv)))
            x_drag, _ = _iterate(dragged, x0)
            # Keep the real constraints exact: polish without the drag target.
            x, r = _iterate(system, x_drag)
            if r.size == 0 or float(np.max(np.abs(r))) < TOLERANCE:
                return self._finish(system, x, r)
        x, r = _iterate(system, x0)
        return self._finish(system, x, r)

    @staticmethod
    def _finish(system: _System, x: Vec, r: Vec) -> SolveResult:
        n = len(x)
        if r.size and float(np.max(np.abs(r))) >= TOLERANCE:
            failed: list[str] = []
            offset = 0
            for block in system.blocks:
                size = len(block.fn(x))
                if block.owner and np.max(np.abs(r[offset : offset + size])) >= TOLERANCE:
                    failed.append(block.owner)
                offset += size
            points, radii = _result(system, system.x0)
            return SolveResult(Status.FAILED, points, radii, failed=failed)
        points, radii = _result(system, x)
        if r.size == 0:
            return SolveResult(Status.UNDERDETERMINED if n else Status.SOLVED, points, radii, dof=n)
        jac = system.jacobian(x)
        singular = np.linalg.svd(jac, compute_uv=False)
        rank = int(np.sum(singular > 1e-8 * max(1.0, float(singular[0]))))
        dof = n - rank
        if rank < len(r):
            status = Status.REDUNDANT
        elif dof > 0:
            status = Status.UNDERDETERMINED
        else:
            status = Status.SOLVED
        return SolveResult(status, points, radii, dof=dof)
