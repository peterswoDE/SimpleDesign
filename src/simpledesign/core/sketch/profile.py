"""Turn a solved sketch into closed regions (outline + holes) for extrusion.

A region's loops are tuples of segments. A segment is either a line
``(a, b)`` or an arc through three points ``(a, mid, b)``; a full circle is
two half arcs. Everything is rounded, frozen and hashable, so regions can be
part of a cached machining feature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from simpledesign.core.sketch.model import P2, Arc, Circle, ConstraintKind, Line, Sketch

type Segment = tuple[P2, P2] | tuple[P2, P2, P2]
type Loop = tuple[Segment, ...]

_DIGITS = 6
_MERGE = 1e-5  # mm: points closer than this are the same node


class ProfileProblem(StrEnum):
    EMPTY = "empty"
    OPEN = "open"  # a loop is not closed
    BRANCHED = "branched"  # three or more segments meet in one point


class ProfileError(Exception):
    def __init__(self, problem: ProfileProblem) -> None:
        super().__init__(problem)
        self.problem = problem


@dataclass(frozen=True, slots=True)
class Region:
    outer: Loop
    holes: tuple[Loop, ...] = ()


def _r(p: P2) -> P2:
    return (round(p[0], _DIGITS) + 0.0, round(p[1], _DIGITS) + 0.0)


# ====================================================================== arcs
def arc_geometry(a: P2, m: P2, b: P2) -> tuple[P2, float, float, float]:
    """Centre, radius, start angle and signed sweep of the arc a -> m -> b."""
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        raise ValueError("collinear arc points")
    ux = (
        (ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)
    ) / d
    uy = (
        (ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)
    ) / d
    radius = math.hypot(ax - ux, ay - uy)
    start = math.atan2(ay - uy, ax - ux)
    mid = math.atan2(by - uy, bx - ux)
    end = math.atan2(cy - uy, cx - ux)
    ccw = (_norm(mid - start)) < (_norm(end - start))
    sweep = _norm(end - start) if ccw else -_norm(start - end)
    if abs(sweep) < 1e-12:  # closed (should not happen with half arcs)
        sweep = 2 * math.pi
    return (ux, uy), radius, start, sweep


def _norm(angle: float) -> float:
    return angle % (2 * math.pi)


def sample(segment: Segment, per_quarter: int = 8) -> list[P2]:
    """Points along a segment (without its end point)."""
    if len(segment) == 2:
        return [segment[0]]
    a, m, b = segment
    (cx, cy), radius, start, sweep = arc_geometry(a, m, b)
    steps = max(2, math.ceil(abs(sweep) / (math.pi / 2) * per_quarter))
    return [
        (
            cx + radius * math.cos(start + sweep * i / steps),
            cy + radius * math.sin(start + sweep * i / steps),
        )
        for i in range(steps)
    ]


def polygon(loop: Loop) -> list[P2]:
    return [p for segment in loop for p in sample(segment)]


def signed_area(points: list[P2]) -> float:
    return 0.5 * sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )


def _inside(p: P2, poly: list[P2]) -> bool:
    x, y = p
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _mostly_inside(points: list[P2], poly: list[P2]) -> bool:
    """Majority vote: robust against points that touch the boundary."""
    inside = sum(_inside(p, poly) for p in points)
    return inside * 2 > len(points)


def loop_bounds(loop: Loop) -> tuple[P2, P2]:
    """Exact bounding box (arcs include their extreme points)."""
    xs: list[float] = []
    ys: list[float] = []
    for segment in loop:
        xs += [segment[0][0], segment[-1][0]]
        ys += [segment[0][1], segment[-1][1]]
        if len(segment) == 3:
            a, m, b = segment
            (cx, cy), radius, start, sweep = arc_geometry(a, m, b)
            for k in range(-8, 9):
                angle = k * math.pi / 2
                offset = (angle - start) if sweep > 0 else (start - angle)
                if 0 <= offset % (2 * math.pi) <= abs(sweep):
                    xs.append(cx + radius * math.cos(angle))
                    ys.append(cy + radius * math.sin(angle))
    return (min(xs), min(ys)), (max(xs), max(ys))


def bounds(regions: tuple[Region, ...]) -> tuple[P2, P2]:
    boxes = [loop_bounds(r.outer) for r in regions]
    return (
        (min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)),
        (max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)),
    )


def translate(regions: tuple[Region, ...], du: float, dv: float) -> tuple[Region, ...]:
    def move(loop: Loop) -> Loop:
        return tuple(tuple(_r((p[0] + du, p[1] + dv)) for p in seg) for seg in loop)  # type: ignore[misc]

    return tuple(Region(move(r.outer), tuple(move(h) for h in r.holes)) for r in regions)


# ====================================================================== loops
class _Nodes:
    """Union-find over point ids, merged by coincidence and position."""

    def __init__(self, sketch: Sketch, positions: dict[str, P2]) -> None:
        self.parent: dict[str, str] = {p: p for p in positions}
        for c in sketch.constraints.values():
            if c.kind == ConstraintKind.COINCIDENT and all(r in self.parent for r in c.refs):
                self.union(*c.refs)
        ids = sorted(positions)
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pa, pb = positions[a], positions[b]
                if abs(pa[0] - pb[0]) < _MERGE and abs(pa[1] - pb[1]) < _MERGE:
                    self.union(a, b)

    def find(self, p: str) -> str:
        while self.parent[p] != p:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        return p

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def loops(sketch: Sketch, positions: dict[str, P2], radii: dict[str, float]) -> list[Loop]:
    """Closed loops of the non-construction geometry."""
    nodes = _Nodes(sketch, positions)
    result: list[Loop] = []
    # edges: (node a, node b, segment from a to b)
    edges: list[tuple[str, str, Segment]] = []
    for entity in sketch.entities.values():
        if entity.construction:
            continue
        if isinstance(entity, Circle):
            cx, cy = positions[entity.center]
            r = radii.get(entity.id, entity.radius)
            if r <= 0:
                raise ProfileError(ProfileProblem.OPEN)
            e, n, w, s = (cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)
            result.append(((_r(e), _r(n), _r(w)), (_r(w), _r(s), _r(e))))
        elif isinstance(entity, Line):
            a, b = positions[entity.start], positions[entity.end]
            edges.append((nodes.find(entity.start), nodes.find(entity.end), (_r(a), _r(b))))
        elif isinstance(entity, Arc):
            c = positions[entity.center]
            a, b = positions[entity.start], positions[entity.end]
            radius = math.hypot(a[0] - c[0], a[1] - c[1])
            t0 = math.atan2(a[1] - c[1], a[0] - c[0])
            t1 = math.atan2(b[1] - c[1], b[0] - c[0])
            sweep = _norm(t1 - t0) or 2 * math.pi
            tm = t0 + sweep / 2
            m = (c[0] + radius * math.cos(tm), c[1] + radius * math.sin(tm))
            edges.append((nodes.find(entity.start), nodes.find(entity.end), (_r(a), _r(m), _r(b))))

    adjacency: dict[str, list[int]] = {}
    for i, (na, nb, _seg) in enumerate(edges):
        adjacency.setdefault(na, []).append(i)
        adjacency.setdefault(nb, []).append(i)
    for incident in adjacency.values():
        degree = sum(2 if edges[i][0] == edges[i][1] else 1 for i in incident)
        if degree == 1:
            raise ProfileError(ProfileProblem.OPEN)
        if degree > 2:
            raise ProfileError(ProfileProblem.BRANCHED)

    used: set[int] = set()
    for first in range(len(edges)):
        if first in used:
            continue
        loop: list[Segment] = []
        start, current = edges[first][0], edges[first][1]
        loop.append(edges[first][2])
        used.add(first)
        while current != start:
            nxt = next(i for i in adjacency[current] if i not in used)
            na, nb, segment = edges[nxt]
            used.add(nxt)
            if na == current:
                loop.append(segment)
                current = nb
            else:
                loop.append(tuple(reversed(segment)))  # type: ignore[arg-type]
                current = na
        # Make consecutive segments share exact end points.
        fixed: list[Segment] = []
        for i, segment in enumerate(loop):
            following = loop[(i + 1) % len(loop)]
            fixed.append((*segment[:-1], following[0]))
        result.append(tuple(fixed))
    return result


def regions(
    sketch: Sketch, positions: dict[str, P2], radii: dict[str, float]
) -> tuple[Region, ...]:
    """Nest the loops into outlines with holes (even-odd)."""
    found = loops(sketch, positions, radii)
    if not found:
        raise ProfileError(ProfileProblem.EMPTY)
    polys = [polygon(loop) for loop in found]
    areas = [abs(signed_area(p)) for p in polys]
    containers: list[list[int]] = []
    for i, poly in enumerate(polys):
        containers.append(
            [
                j
                for j in range(len(found))
                if j != i and areas[j] > areas[i] and _mostly_inside(poly, polys[j])
            ]
        )
    outers = [i for i in range(len(found)) if len(containers[i]) % 2 == 0]
    holes: dict[int, list[Loop]] = {i: [] for i in outers}
    for i in range(len(found)):
        if len(containers[i]) % 2 == 1:
            parent = min(containers[i], key=lambda j: areas[j])
            holes[parent].append(found[i])
    return tuple(Region(found[i], tuple(holes[i])) for i in sorted(outers, key=lambda i: -areas[i]))
