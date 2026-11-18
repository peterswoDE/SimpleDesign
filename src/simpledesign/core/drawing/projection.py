"""Hidden-line projection of parts into 2D views (OCC HLR).

A :class:`View` is a direction to look from plus the paper "right" axis. The
projection returns visible and hidden edges as polylines in view
coordinates (mm, model scale), and the circles it found (for hole
dimensions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from OCP.TopoDS import TopoDS_Shape

from simpledesign.core.model.state import Vec3

type P2 = tuple[float, float]

DEFLECTION = 0.05  # mm: chord error when curves are turned into polylines


class ViewKind(StrEnum):
    FRONT = "front"  # Vorderansicht: from -Y, X right, Z up
    LEFT = "left"  # Seitenansicht von links: from -X, -Y right, Z up
    TOP = "top"  # Draufsicht: from +Z, X right, Y up
    ISO = "iso"  # from front-right-above


@dataclass(frozen=True, slots=True)
class View:
    toward_viewer: Vec3  # unit-ish vector from the model toward the eye
    right: Vec3  # paper x axis (perpendicular to toward_viewer)

    @property
    def up(self) -> Vec3:
        n, r = self.toward_viewer, self.right
        return (
            n[1] * r[2] - n[2] * r[1],
            n[2] * r[0] - n[0] * r[2],
            n[0] * r[1] - n[1] * r[0],
        )

    def to_2d(self, p: Vec3) -> P2:
        r, u = self.right, self.up
        nr = math.sqrt(sum(c * c for c in r))
        nu = math.sqrt(sum(c * c for c in u))
        return (
            sum(a * b for a, b in zip(p, r, strict=True)) / nr,
            sum(a * b for a, b in zip(p, u, strict=True)) / nu,
        )


STANDARD_VIEWS: dict[ViewKind, View] = {
    ViewKind.FRONT: View((0, -1, 0), (1, 0, 0)),
    ViewKind.LEFT: View((-1, 0, 0), (0, -1, 0)),
    ViewKind.TOP: View((0, 0, 1), (1, 0, 0)),
    ViewKind.ISO: View((1, -1, 1), (1, 1, 0)),
}


@dataclass(frozen=True, slots=True)
class Circle2D:
    center: P2
    radius: float


@dataclass(slots=True)
class Projection:
    visible: list[list[P2]] = field(default_factory=list)
    hidden: list[list[P2]] = field(default_factory=list)
    circles: list[Circle2D] = field(default_factory=list)  # visible full circles

    def bounds(self) -> tuple[P2, P2]:
        points = [p for line in self.visible + self.hidden for p in line]
        if not points:
            return (0.0, 0.0), (0.0, 0.0)
        us = [p[0] for p in points]
        vs = [p[1] for p in points]
        return (min(us), min(vs)), (max(us), max(vs))

    def translated(self, du: float, dv: float) -> Projection:
        def move(lines: list[list[P2]]) -> list[list[P2]]:
            return [[(u + du, v + dv) for u, v in line] for line in lines]

        return Projection(
            move(self.visible),
            move(self.hidden),
            [Circle2D((c.center[0] + du, c.center[1] + dv), c.radius) for c in self.circles],
        )


def project(shapes: list[TopoDS_Shape], view: View, hidden: bool = True) -> Projection:
    """Visible (and hidden) edges of all ``shapes`` seen together from ``view``."""
    from OCP.BRep import BRep_Builder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCP.TopoDS import TopoDS_Compound

    result = Projection()
    if not shapes:
        return result
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)

    algo = HLRBRep_Algo()
    algo.Add(compound)
    axes = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*view.toward_viewer), gp_Dir(*view.right))
    algo.Projector(HLRAlgo_Projector(axes))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)

    for compound_of in (hlr.VCompound, hlr.OutLineVCompound):
        _collect(compound_of(), result.visible, result.circles)
    if hidden:
        for compound_of in (hlr.HCompound, hlr.OutLineHCompound):
            _collect(compound_of(), result.hidden, None)
        result.hidden = _drop_covered(result.hidden, result.visible)
    return result


def _drop_covered(
    hidden: list[list[P2]], visible: list[list[P2]], tol: float = 1e-3
) -> list[list[P2]]:
    """Hidden straight edges lying on a visible edge (e.g. the back outline of
    a box) are not drawn: dashes over a solid line only blur the drawing."""
    segments = [(line[0], line[-1]) for line in visible if len(line) == 2]

    def on(p: P2, a: P2, b: P2) -> bool:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 < tol * tol:
            return math.dist(p, a) < tol
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2
        if t < -tol or t > 1 + tol:
            return False
        return math.dist(p, (a[0] + t * dx, a[1] + t * dy)) < tol

    kept = []
    for line in hidden:
        if len(line) == 2 and any(on(line[0], a, b) and on(line[1], a, b) for a, b in segments):
            continue
        kept.append(line)
    return kept


def _collect(shape: TopoDS_Shape, lines: list[list[P2]], circles: list[Circle2D] | None) -> None:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_CurveType
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if shape is None or shape.IsNull():
        return
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        explorer.Next()
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        kind = curve.GetType()
        if kind == GeomAbs_CurveType.GeomAbs_Line:
            a, b = curve.Value(first), curve.Value(last)
            lines.append([(a.X(), a.Y()), (b.X(), b.Y())])
            continue
        full = abs(last - first - 2 * math.pi) < 1e-6
        if kind == GeomAbs_CurveType.GeomAbs_Circle and circles is not None and full:
            circ = curve.Circle()
            c = circ.Location()
            circles.append(Circle2D((c.X(), c.Y()), circ.Radius()))
        sampler = GCPnts_QuasiUniformDeflection(curve, DEFLECTION, first, last)
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            continue
        points = []
        for i in range(1, sampler.NbPoints() + 1):
            p = sampler.Value(i)
            points.append((p.X(), p.Y()))
        lines.append(points)
