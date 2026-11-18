"""2D sketches: points, lines, circles, arcs and constraints between them.

A sketch lives in a plane with coordinates ``(u, v)`` in millimetres. The
stored point coordinates are the *last valid* solution: the solver starts
from them, and if a sketch cannot be solved they are what is shown.

Dimension constraints get their value from a formula. The formula is not
stored here but in the owning board (``Board.expressions["d.<id>"]``), so
renaming, deleting and copying treat it like every other formula.

Every sketch has *reference geometry* that the user can constrain against but
not move: the origin ``o`` with the axes ``ox``/``oy`` and - for sketches on
a part face - the face rectangle (corners ``r1``-``r3``, edges ``e0``-``e3``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from simpledesign.core.ids import new_id

type P2 = tuple[float, float]
type Json = dict[str, Any]

ORIGIN = "o"


class ConstraintKind(StrEnum):
    COINCIDENT = "coincident"  # two points
    ON = "on"  # point on line / circle / arc
    HORIZONTAL = "horizontal"  # line
    VERTICAL = "vertical"  # line
    PARALLEL = "parallel"  # two lines
    PERPENDICULAR = "perpendicular"  # two lines
    TANGENT = "tangent"  # line + circle/arc, or two circles/arcs
    EQUAL = "equal"  # two lines (length) or two circles/arcs (radius)
    FIX = "fix"  # point stays where it is
    # Dimensions (value from a formula)
    DISTANCE = "distance"  # two points, or one line (its length)
    HDISTANCE = "hdistance"  # two points, along u
    VDISTANCE = "vdistance"  # two points, along v
    RADIUS = "radius"  # circle / arc
    ANGLE = "angle"  # two lines, degrees


DIMENSIONS = frozenset(
    {
        ConstraintKind.DISTANCE,
        ConstraintKind.HDISTANCE,
        ConstraintKind.VDISTANCE,
        ConstraintKind.RADIUS,
        ConstraintKind.ANGLE,
    }
)


@dataclass(slots=True)
class Point:
    id: str
    u: float
    v: float

    @property
    def pos(self) -> P2:
        return (self.u, self.v)


@dataclass(slots=True)
class Line:
    id: str
    start: str
    end: str
    construction: bool = False


@dataclass(slots=True)
class Circle:
    id: str
    center: str
    radius: float
    construction: bool = False


@dataclass(slots=True)
class Arc:
    """Counter-clockwise from ``start`` to ``end`` around ``center``."""

    id: str
    center: str
    start: str
    end: str
    construction: bool = False


type Entity = Line | Circle | Arc


@dataclass(slots=True)
class Constraint:
    id: str
    kind: ConstraintKind
    refs: tuple[str, ...]
    # FIX: the fixed position; TANGENT between circles: 1 = outside, -1 = inside;
    # ANGLE/H/VDISTANCE: orientation chosen when created (keeps the solution from flipping).
    data: tuple[float, ...] = ()

    @property
    def is_dimension(self) -> bool:
        return self.kind in DIMENSIONS

    @property
    def formula_key(self) -> str:
        """Key of the dimension formula in the owning board's expressions."""
        return f"d.{self.id}"


@dataclass(slots=True)
class Sketch:
    points: dict[str, Point] = field(default_factory=dict)
    entities: dict[str, Entity] = field(default_factory=dict)
    constraints: dict[str, Constraint] = field(default_factory=dict)

    # ------------------------------------------------------------------ build
    def add_point(self, u: float, v: float) -> Point:
        point = Point(new_id(), float(u), float(v))
        self.points[point.id] = point
        return point

    def add_line(self, start: str, end: str, construction: bool = False) -> Line:
        line = Line(new_id(), start, end, construction)
        self.entities[line.id] = line
        return line

    def add_circle(self, center: str, radius: float, construction: bool = False) -> Circle:
        circle = Circle(new_id(), center, float(radius), construction)
        self.entities[circle.id] = circle
        return circle

    def add_arc(self, center: str, start: str, end: str, construction: bool = False) -> Arc:
        arc = Arc(new_id(), center, start, end, construction)
        self.entities[arc.id] = arc
        return arc

    def add_constraint(
        self, kind: ConstraintKind, refs: tuple[str, ...], data: tuple[float, ...] = ()
    ) -> Constraint:
        constraint = Constraint(new_id(), kind, refs, data)
        self.constraints[constraint.id] = constraint
        return constraint

    def add_rectangle(self, a: P2, b: P2) -> list[Line]:
        """Four lines between corners ``a`` and ``b``, horizontal/vertical constrained."""
        (u0, v0), (u1, v1) = a, b
        corners = [self.add_point(u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
        lines = [self.add_line(corners[i].id, corners[(i + 1) % 4].id) for i in range(4)]
        for i, line in enumerate(lines):
            kind = ConstraintKind.HORIZONTAL if i % 2 == 0 else ConstraintKind.VERTICAL
            self.add_constraint(kind, (line.id,))
        return lines

    # ------------------------------------------------------------------ queries
    def point_ids_of(self, entity_id: str) -> tuple[str, ...]:
        entity = self.entities[entity_id]
        if isinstance(entity, Line):
            return (entity.start, entity.end)
        if isinstance(entity, Circle):
            return (entity.center,)
        return (entity.center, entity.start, entity.end)

    def users_of(self, obj_id: str) -> set[str]:
        """Entities and constraints that use a point or entity."""
        users = {e.id for e in self.entities.values() if obj_id in self.point_ids_of(e.id)}
        users |= {c.id for c in self.constraints.values() if obj_id in c.refs}
        return users

    def delete(self, ids: set[str]) -> None:
        """Delete points, entities, constraints and everything that depends on them."""
        pending = set(ids)
        while pending:
            obj_id = pending.pop()
            if obj_id in self.points:
                del self.points[obj_id]
            elif obj_id in self.entities:
                del self.entities[obj_id]
            elif obj_id in self.constraints:
                del self.constraints[obj_id]
                continue
            else:
                continue
            pending |= {u for u in self.users_of(obj_id) if u not in pending}
        # Points nothing uses any more go too (unless constrained on their own).
        used = {p for e in self.entities for p in self.point_ids_of(e)}
        used |= {r for c in self.constraints.values() for r in c.refs}
        for point_id in [p for p in self.points if p not in used]:
            del self.points[point_id]

    def dimensions(self) -> list[Constraint]:
        return [c for c in self.constraints.values() if c.is_dimension]

    def copy(self) -> Sketch:
        return copy.deepcopy(self)


# ====================================================================== json
def to_dict(sketch: Sketch) -> Json:
    entities: list[Json] = []
    for e in sketch.entities.values():
        if isinstance(e, Line):
            entities.append({"type": "line", "id": e.id, "points": [e.start, e.end]})
        elif isinstance(e, Circle):
            entities.append(
                {"type": "circle", "id": e.id, "points": [e.center], "radius": e.radius}
            )
        else:
            entities.append({"type": "arc", "id": e.id, "points": [e.center, e.start, e.end]})
        entities[-1]["construction"] = e.construction
    return {
        "points": [[p.id, p.u, p.v] for p in sketch.points.values()],
        "entities": entities,
        "constraints": [
            {"id": c.id, "kind": c.kind.value, "refs": list(c.refs), "data": list(c.data)}
            for c in sketch.constraints.values()
        ],
    }


def from_dict(data: Json) -> Sketch:
    """Raises KeyError/TypeError/ValueError for invalid data."""
    sketch = Sketch()
    for point_id, u, v in data["points"]:
        sketch.points[str(point_id)] = Point(str(point_id), float(u), float(v))
    for e in data["entities"]:
        ids = [str(p) for p in e["points"]]
        construction = bool(e.get("construction", False))
        entity: Entity
        if e["type"] == "line":
            entity = Line(e["id"], ids[0], ids[1], construction)
        elif e["type"] == "circle":
            entity = Circle(e["id"], ids[0], float(e["radius"]), construction)
        elif e["type"] == "arc":
            entity = Arc(e["id"], ids[0], ids[1], ids[2], construction)
        else:
            raise ValueError(f"unknown sketch entity {e['type']!r}")
        for point_id in ids:
            if point_id not in sketch.points:
                raise ValueError(f"unknown point {point_id}")
        sketch.entities[entity.id] = entity
    for c in data["constraints"]:
        constraint = Constraint(
            c["id"],
            ConstraintKind(c["kind"]),
            tuple(str(r) for r in c["refs"]),
            tuple(float(x) for x in c.get("data", ())),
        )
        sketch.constraints[constraint.id] = constraint
    return sketch
