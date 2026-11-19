"""CNC export: one DXF per part for nesting routers (ezdxf, MIT licensed).

Every distinct part (board, machining) becomes ``<Teil>.dxf``; parts machined
differently get ``<Teil>_2.dxf`` … The part lies flat, seen from the top face
(the thickness side): X runs along the length (= grain), Y along the width,
origin at the corner of the contour. Units are millimetres.

The contour is the rough part (with joint extensions) minus the edge banding
thickness of banded edges, i.e. the size the router cuts. Each machining lies
on a layer of its own, as nesting software assigns tools and depths by layer:

``KONTUR``                 outline of the part
``AUSSCHNITT``             cut-outs through the whole thickness
``BOHRUNG_D<Ø>_T<Tiefe>``  holes from the top face (``T`` = depth, ``DURCH``)
``TASCHE_T<Tiefe>``        pockets, grooves, rabbets from the top face
``UNTEN_…``                the same from the bottom face (seen from the top)
``KANTE_…``                machining in the edges (horizontal holes, mortises):
                           for information, not for the router
``INFO``                   part name, material, size and quantity as text
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ezdxf import units
from ezdxf.filemanagement import new

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.machining import (
    Cutout,
    Extrusion,
    Hole,
    PartShape,
    Prism,
    local_shape,
)
from simpledesign.core.model.part import Board, band_edge_side
from simpledesign.core.model.state import Vec3
from simpledesign.core.sketch.profile import polygon

type P2 = tuple[float, float]

EPS = 0.01
CONTOUR = "KONTUR"
THROUGH = "AUSSCHNITT"
BOTTOM = "UNTEN_"
EDGE = "KANTE_"
INFO = "INFO"
# ACI colours, so the layers can be told apart in a viewer
_COLOURS = {CONTOUR: 7, THROUGH: 1, "BOHRUNG": 4, "TASCHE": 3, INFO: 8}


def _num(value: float) -> str:
    return f"{round(value, 2):g}"


@dataclass(slots=True)
class CncPart:
    """One part flat in its own frame: everything in (x, y) of the top view."""

    name: str
    material: str
    length: float
    width: float
    thickness: float
    count: int = 1
    contour: list[list[P2]] = field(default_factory=list)  # outer loop(s)
    circles: list[tuple[str, P2, float]] = field(default_factory=list)  # layer, centre, r
    polylines: list[tuple[str, list[P2], bool]] = field(default_factory=list)  # layer, closed

    @property
    def layers(self) -> set[str]:
        return (
            {CONTOUR}
            | {layer for layer, _c, _r in self.circles}
            | {layer for layer, _p, _closed in self.polylines}
        )


# ---------------------------------------------------------------- geometry
def _hull(points: Iterable[P2]) -> list[P2]:
    """Convex hull, counter-clockwise (monotone chain)."""
    pts = sorted({(round(x, 4), round(y, 4)) for x, y in points})
    if len(pts) < 3:
        return pts

    def cross(o: P2, a: P2, b: P2) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[P2] = []
    upper: list[P2] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _rect(lo: P2, hi: P2) -> list[P2]:
    return [lo, (hi[0], lo[1]), hi, (lo[0], hi[1])]


class _Frame:
    """World (relative to the rough box) -> part top view."""

    def __init__(self, doc: Document, board: Board, shape: PartShape) -> None:
        o = board.orientation
        self.perm = (o.length_axis.position, o.width_axis.position, o.thickness_axis.position)
        (length, width, thickness), _signature = local_shape(shape, o)
        lo = [0.0, 0.0]
        hi = [length, width]
        for edge, band_id in board.edges.items():
            axis, direction = band_edge_side(o, edge)
            k = 0 if axis == o.length_axis else 1
            t = doc.edge_band(band_id).thickness
            if direction < 0:
                lo[k] += t
            else:
                hi[k] -= t
        self.offset = (lo[0], lo[1])
        self.size = (hi[0] - lo[0], hi[1] - lo[1])
        self.thickness = thickness

    def local(self, p: Sequence[float]) -> Vec3:
        u, v, t = (p[i] for i in self.perm)
        return (u - self.offset[0], v - self.offset[1], t)

    def flat(self, p: Sequence[float]) -> P2:
        u, v, _t = self.local(p)
        return (u, v)

    def side(self, t_lo: float, t_hi: float) -> tuple[str, float] | None:
        """Layer prefix and depth of something removed between ``t_lo`` and
        ``t_hi`` (thickness direction); None when it reaches neither face."""
        if t_lo <= EPS and t_hi >= self.thickness - EPS:
            return "", math.inf
        if t_hi >= self.thickness - EPS:
            return "", self.thickness - t_lo
        if t_lo <= EPS:
            return BOTTOM, t_hi
        return None


def _depth(depth: float) -> str:
    return "DURCH" if math.isinf(depth) else _num(depth)


def _area(frame: _Frame, part: CncPart, outline: list[P2], t_lo: float, t_hi: float) -> None:
    side = frame.side(t_lo, t_hi)
    if side is None:
        part.polylines.append((f"{EDGE}TASCHE", outline, True))
    elif math.isinf(side[1]):
        part.polylines.append((THROUGH, outline, True))
    else:
        part.polylines.append((f"{side[0]}TASCHE_T{_depth(side[1])}", outline, True))


def _hole(frame: _Frame, part: CncPart, hole: Hole) -> None:
    axis = frame.perm.index(hole.axis)
    centre = frame.local(hole.center)
    d = _num(hole.diameter)
    if axis == 2:
        through = hole.depth >= frame.thickness - EPS
        depth = "DURCH" if through else _num(hole.depth)
        prefix = BOTTOM if hole.direction > 0 and not through else ""
        part.circles.append(
            (f"{prefix}BOHRUNG_D{d}_T{depth}", (centre[0], centre[1]), hole.diameter / 2)
        )
        return
    end = list(centre)
    end[axis] += hole.direction * hole.depth
    line = [(centre[0], centre[1]), (end[0], end[1])]
    part.polylines.append((f"{EDGE}BOHRUNG_D{d}_T{_num(hole.depth)}", line, False))


def _prism(frame: _Frame, part: CncPart, prism: Prism) -> None:
    swept = [
        (*p[: prism.axis], p[prism.axis] + prism.length, *p[prism.axis + 1 :]) for p in prism.points
    ]
    local = [frame.local(p) for p in (*prism.points, *swept)]
    outline = _hull((p[0], p[1]) for p in local)
    _area(frame, part, outline, min(p[2] for p in local), max(p[2] for p in local))


def _extrusion_loops(frame: _Frame, ext: Extrusion) -> tuple[list[list[P2]], float, float]:
    a, b, c = ext.axes
    loops: list[list[P2]] = []

    def world(u: float, v: float, w: float) -> Vec3:
        p = list(ext.origin)
        p[a] += u
        p[b] += v
        p[c] += w
        return (p[0], p[1], p[2])

    for region in ext.regions:
        for loop in (region.outer, *region.holes):
            loops.append([frame.flat(world(u, v, 0.0)) for u, v in polygon(loop)])
    ts = [frame.local(world(0.0, 0.0, w))[2] for w in (0.0, ext.length)]
    return loops, min(ts), max(ts)


def cnc_part(doc: Document, board: Board, shape: PartShape, count: int = 1) -> CncPart:
    frame = _Frame(doc, board, shape)
    length, width = frame.size
    part = CncPart(
        board.name,
        doc.materials[board.material_id].name,
        length,
        width,
        frame.thickness,
        count,
    )
    part.contour = [_rect((0.0, 0.0), (length, width))]
    for f in shape.features:
        if isinstance(f, Hole):
            _hole(frame, part, f)
        elif isinstance(f, Cutout):
            lo, hi = frame.local(f.lo), frame.local(f.hi)
            outline = _rect(
                (max(lo[0], 0.0), max(lo[1], 0.0)), (min(hi[0], length), min(hi[1], width))
            )
            _area(frame, part, outline, lo[2], hi[2])
        elif isinstance(f, Prism):
            _prism(frame, part, f)
        else:
            loops, t_lo, t_hi = _extrusion_loops(frame, f)
            flat = frame.perm.index(f.axes[2]) == 2
            if not f.cut and flat:  # profile part: the sketch is the contour
                part.contour = []
                k = 0
                for region in f.regions:
                    part.contour.append(loops[k])
                    holes = loops[k + 1 : k + 1 + len(region.holes)]
                    part.polylines += [(THROUGH, h, True) for h in holes]
                    k += 1 + len(region.holes)
            elif f.cut and flat:
                for loop in loops:
                    _area(frame, part, loop, t_lo, t_hi)
            elif f.cut:
                for loop in loops:
                    part.polylines.append((f"{EDGE}TASCHE", _hull(loop), True))
    return part


# ---------------------------------------------------------------- collecting
def cnc_parts(doc: Document, evaluation: Evaluation) -> list[CncPart]:
    """One entry per distinct part (copies machined the same are counted)."""
    result: list[CncPart] = []
    for board in doc.boards.values():
        seen: dict[object, CncPart] = {}
        for shape in evaluation.machining.parts[board.id]:
            key = local_shape(shape, board.orientation)
            if key in seen:
                seen[key].count += 1
                continue
            seen[key] = part = cnc_part(doc, board, shape)
            if len(seen) > 1:
                part.name = f"{board.name}_{len(seen)}"
            result.append(part)
    return result


def file_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "Teil"


def write_part(part: CncPart, path: Path) -> None:
    doc = new("R2018", setup=True)
    doc.units = units.MM
    for layer in sorted(part.layers | {INFO}):
        key = next((k for k in _COLOURS if layer.removeprefix(BOTTOM).startswith(k)), "")
        colour = 9 if layer.startswith(EDGE) else _COLOURS.get(key, 7)
        doc.layers.add(layer, color=colour)
    msp = doc.modelspace()
    for loop in part.contour:
        msp.add_lwpolyline(loop, close=True, dxfattribs={"layer": CONTOUR})
    for layer, centre, radius in part.circles:
        msp.add_circle(centre, radius, dxfattribs={"layer": layer})
    for layer, points, closed in part.polylines:
        msp.add_lwpolyline(points, close=closed, dxfattribs={"layer": layer})
    info = (
        f"{part.name} | {part.material} | {_num(part.length)} x {_num(part.width)} x "
        f"{_num(part.thickness)} | {part.count} Stk."
    )
    msp.add_text(info, height=5.0, dxfattribs={"layer": INFO, "insert": (0.0, -10.0)})
    doc.saveas(path)


def write_cnc(doc: Document, evaluation: Evaluation, folder: Path) -> list[Path]:
    """Write one DXF per distinct part into ``folder``; returns the files."""
    written: list[Path] = []
    used: set[str] = set()
    for part in cnc_parts(doc, evaluation):
        stem = file_name(part.name)
        name, k = stem, 1
        while name.lower() in used:
            k += 1
            name = f"{stem}_{k}"
        used.add(name.lower())
        path = folder / f"{name}.dxf"
        write_part(part, path)
        written.append(path)
    return written
