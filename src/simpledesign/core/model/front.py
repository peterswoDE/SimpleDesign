"""Doors and drawers fitted into an opening at the front of a carcass.

The opening is found like a compartment (:mod:`fit`), but in a thin slab
just behind the front edge: shelves set back from the front do not split it.
Every size and position is a formula of the faces around, so doors and
drawers keep fitting when the furniture changes.

* overlay ("aufliegend"): the front lies on the carcass edges. Towards the
  outside it reaches the outer face of the part, towards a part shared with
  the next opening (middle wall, fixed shelf) only its middle. Every edge
  keeps half the gap.
* inset ("einliegend"): the front sits in the opening, flush with the
  carcass front, the full gap all round.

A drawer is a front plus a box (bottom screwed underneath, two sides, front
and back piece between the sides), with room for the slides at both sides.
Several drawers stacked in one opening are a pattern ("Anordnung").

Doors get hinges on the side they swing on (both sides for a pair of doors),
drawers a slide on each side (:mod:`fitting`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from simpledesign.core.model import fit
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.fitting import FittingKind
from simpledesign.core.model.part import Axis, Orientation

type Vec3 = tuple[float, float, float]

X, Y, Z = 0, 1, 2
SLAB = 1.0  # mm: depth of the slab behind the front edge that bounds an opening


class FrontKind(StrEnum):
    DOOR = "door"
    DRAWER = "drawer"


class Mounting(StrEnum):
    OVERLAY = "overlay"  # aufliegend
    INSET = "inset"  # einliegend


@dataclass(slots=True)
class FrontSettings:
    mounting: Mounting = Mounting.OVERLAY
    gap: float = 3.0  # Fuge between fronts / around a front
    leaves: int = 1  # door: 1 or 2 (zweiflügelig)
    hinge_right: bool = False  # one leaf: hinges on the right side
    whole: bool = False  # door over the whole furniture (ignore inner parts)
    drawers: int = 1  # stacked in the opening
    slide_clearance: float = 13.0  # per side, for the slides (Auszugsluft)
    bottom_gap: float = 10.0  # from the opening floor to the drawer box
    top_gap: float = 25.0  # above the drawer box
    back_gap: float = 10.0  # behind the drawer box
    box_material_id: str = "builtin.multiplex_birke_12"
    bottom_material_id: str = "builtin.hdf_3"


@dataclass(frozen=True, slots=True)
class Term:
    """A formula with its current value (for the preview)."""

    formula: str
    value: float

    def plus(self, d: float) -> Term:
        if abs(d) < 1e-12:
            return self
        sign = "+" if d > 0 else "-"
        return Term(f"{self.formula} {sign} {abs(d):g}", self.value + d)

    def minus(self, other: Term) -> Term:
        return Term(f"{self.formula} - {fit.ref(other.formula)}", self.value - other.value)

    def over(self, n: int) -> Term:
        return self if n == 1 else Term(f"({self.formula}) / {n}", self.value / n)


def _face(face: fit.Face) -> Term:
    return Term(face.formula, face.value)


@dataclass(slots=True)
class Opening:
    inner_lo: dict[int, Term]  # per axis X, Z: faces of the parts around
    inner_hi: dict[int, Term]
    outer_lo: dict[int, Term]  # overlay limits (outer face or middle of a shared part)
    outer_hi: dict[int, Term]
    front: Term  # front edge of the carcass (Y)
    back: Term  # face behind the opening (Y)
    board_ids: list[str]
    sides: dict[int, str] = field(default_factory=dict)  # -1/+1: the parts left/right

    def inner(self, axis: int) -> Term:
        return self.inner_hi[axis].minus(self.inner_lo[axis])


def opening(
    doc: Document, evaluation: Evaluation, comp: fit.Compartment, point: Vec3, whole: bool = False
) -> Opening | None:
    """The front opening of ``comp`` around ``point`` (None: not closed on all
    four sides). ``whole``: walk past inner parts to the outermost ones."""
    found = fit.boxes(doc, evaluation)
    front, back = comp.lo[Y], comp.hi[Y]
    if back.value - front.value < SLAB:
        return None
    slab = (front.value, front.value + SLAB)
    q = (point[X], front.value + SLAB / 2, point[Z])
    result = Opening({}, {}, {}, {}, _face(front), _face(back), [])
    for axis, other in ((X, Z), (Z, X)):
        ranges = {other: fit.point_range(q, other), Y: slab}
        for direction in (-1, 1):
            box = fit.cast_box(found, q, axis, direction, ranges)
            if box is None:
                return None
            beyond = _beyond(found, box, q, axis, direction, ranges)
            while whole and beyond is not None:
                box, beyond = beyond, _beyond(found, beyond, q, axis, direction, ranges)
            inner = _face(box.face(axis, high=direction < 0))
            if beyond is None:
                outer = _face(box.face(axis, high=direction > 0))
            else:
                lo, hi = box.lo_formula[axis], box.hi_formula[axis]
                outer = Term(f"({lo} + {hi}) / 2", (box.lo[axis] + box.hi[axis]) / 2)
            if direction < 0:
                result.inner_lo[axis], result.outer_lo[axis] = inner, outer
            else:
                result.inner_hi[axis], result.outer_hi[axis] = inner, outer
            if axis == X:
                result.sides[direction] = box.board_id
            if box.board_id not in result.board_ids:
                result.board_ids.append(box.board_id)
    if min(result.inner(X).value, result.inner(Z).value) < fit.MIN_GAP:
        return None
    return result


def _beyond(
    found: list[fit.Box],
    box: fit.Box,
    q: Vec3,
    axis: int,
    direction: int,
    ranges: dict[int, fit.Range],
) -> fit.Box | None:
    """The next part behind ``box`` (seen from the opening)."""
    far = list(q)
    far[axis] = box.hi[axis] if direction > 0 else box.lo[axis]
    return fit.cast_box(found, (far[0], far[1], far[2]), axis, direction, ranges)


# ====================================================================== plan
@dataclass(slots=True)
class PartPlan:
    key: str  # placeholder in formulas: "{key}" is replaced by the part's name
    base_name: str
    orientation: Orientation
    material_id: str
    thickness: Term
    expressions: dict[str, str]  # length, width, x, y, z (+ count, spacing)
    boxes: list[tuple[Vec3, Vec3]]  # world (lo, size) of every copy, for the preview
    pattern_axis: Axis = Axis.Z


@dataclass(slots=True)
class FrontPlan:
    kind: FrontKind
    parts: list[PartPlan] = field(default_factory=list)
    board_ids: list[str] = field(default_factory=list)  # the parts around
    # (kind, part key, carrier board): fittings that come with the parts
    fittings: list[tuple[FittingKind, str, str]] = field(default_factory=list)

    def boxes(self) -> list[tuple[Vec3, Vec3]]:
        return [box for part in self.parts for box in part.boxes]


def _vec(values: dict[int, float]) -> Vec3:
    return (values[X], values[Y], values[Z])


def _part(
    key: str,
    base_name: str,
    orientation: Orientation,
    material_id: str,
    thickness: Term,
    start: dict[int, Term],
    size: dict[int, Term],
    count: int = 1,
    pattern: tuple[int, Term] | None = None,
) -> PartPlan:
    """A part from its world start and size per axis (the thickness axis is
    taken from ``thickness``)."""
    t_axis = orientation.thickness_axis.position
    size = {**size, t_axis: thickness}
    expressions = {"xyz"[axis]: start[axis].formula for axis in (X, Y, Z)}
    for axis in (X, Y, Z):
        if axis != t_axis:
            dim = orientation.dimension_along((Axis.X, Axis.Y, Axis.Z)[axis])
            expressions[dim] = size[axis].formula
    lo = {axis: start[axis].value for axis in (X, Y, Z)}
    sizes = _vec({axis: size[axis].value for axis in (X, Y, Z)})
    boxes = []
    pattern_axis = Axis.Z
    if pattern is not None and count > 1:
        axis, spacing = pattern
        pattern_axis = (Axis.X, Axis.Y, Axis.Z)[axis]
        expressions["count"] = str(count)
        expressions["spacing"] = spacing.formula
        for i in range(count):
            copy = dict(lo)
            copy[axis] += i * spacing.value
            boxes.append((_vec(copy), sizes))
    else:
        boxes.append((_vec(lo), sizes))
    return PartPlan(
        key, base_name, orientation, material_id, thickness, expressions, boxes, pattern_axis
    )


def _front_edges(op: Opening, settings: FrontSettings) -> tuple[dict[int, Term], dict[int, Term]]:
    g = settings.gap
    if settings.mounting == Mounting.OVERLAY:
        lo = {axis: op.outer_lo[axis].plus(g / 2) for axis in (X, Z)}
        hi = {axis: op.outer_hi[axis].plus(-g / 2) for axis in (X, Z)}
    else:
        lo = {axis: op.inner_lo[axis].plus(g) for axis in (X, Z)}
        hi = {axis: op.inner_hi[axis].plus(-g) for axis in (X, Z)}
    return lo, hi


def _front_y(op: Opening, settings: FrontSettings, thickness: Term) -> Term:
    if settings.mounting == Mounting.INSET:
        return op.front
    return Term(f"{op.front.formula} - {{front}}.thickness", op.front.value - thickness.value)


def plan_door(
    op: Opening, settings: FrontSettings, material_id: str, thickness: Term
) -> FrontPlan | None:
    lo, hi = _front_edges(op, settings)
    g = settings.gap
    leaves = max(1, min(settings.leaves, 2))
    width = hi[X].minus(lo[X])
    if leaves == 2:
        width = Term(f"({width.formula} - {g:g}) / 2", (width.value - g) / 2)
    height = hi[Z].minus(lo[Z])
    if min(width.value, height.value) < fit.MIN_GAP:
        return None
    start = {X: lo[X], Y: _front_y(op, settings, thickness), Z: lo[Z]}
    spacing = Term(f"{{front}}.width + {g:g}", width.value + g)
    door = _part(
        "front",
        "Tür",
        Orientation.ZX,
        material_id,
        thickness,
        start,
        {X: width, Z: height},
        leaves,
        (X, spacing),
    )
    if leaves == 2:
        hinged = [op.sides[-1], op.sides[1]]
    else:
        hinged = [op.sides[1 if settings.hinge_right else -1]]
    hinges = [(FittingKind.HINGE, "front", carrier) for carrier in hinged]
    return FrontPlan(FrontKind.DOOR, [door], list(op.board_ids), hinges)


def plan_drawer(
    op: Opening,
    settings: FrontSettings,
    material_id: str,
    thickness: Term,
    box_thickness: float,
    bottom_thickness: float,
) -> FrontPlan | None:
    s = settings
    n = max(1, settings.drawers)
    lo, hi = _front_edges(op, s)
    width = hi[X].minus(lo[X])
    height = hi[Z].minus(lo[Z])
    if n > 1:
        height = Term(
            f"({height.formula} - {(n - 1) * s.gap:g}) / {n}", (height.value - (n - 1) * s.gap) / n
        )
    front_y = _front_y(op, s, thickness)
    front = _part(
        "front",
        "Schubladenfront",
        Orientation.XZ,
        material_id,
        thickness,
        {X: lo[X], Y: front_y, Z: lo[Z]},
        {X: width, Z: height},
        n,
        (Z, Term(f"{{front}}.width + {s.gap:g}", height.value + s.gap)),
    )

    # the box, below each other in equal slots of the opening
    slot = op.inner(Z).over(n)
    pattern = (Z, slot)
    box_x = op.inner_lo[X].plus(s.slide_clearance)
    box_width = op.inner(X).plus(-2 * s.slide_clearance)
    box_y = Term("{front}.y2", front_y.value + thickness.value)
    box_depth = Term(
        f"{op.back.formula} - {{front}}.y2 - {s.back_gap:g}",
        op.back.value - box_y.value - s.back_gap,
    )
    tb = Term(f"{bottom_thickness:g}", bottom_thickness)
    ts = Term(f"{box_thickness:g}", box_thickness)
    side_height = Term(
        f"{slot.formula} - {s.bottom_gap + s.top_gap:g} - {{bottom}}.thickness",
        slot.value - s.bottom_gap - s.top_gap - bottom_thickness,
    )
    if (
        min(width.value, height.value) < fit.MIN_GAP
        or box_width.value < 2 * box_thickness + 10
        or box_depth.value < 2 * box_thickness + 10
        or side_height.value < 10
    ):
        return None
    z0 = op.inner_lo[Z].plus(s.bottom_gap)
    on_bottom = Term("{bottom}.z2", z0.value + bottom_thickness)
    bottom = _part(
        "bottom",
        "Schubladenboden",
        Orientation.XY,
        s.bottom_material_id,
        tb,
        {X: box_x, Y: box_y, Z: z0},
        {X: box_width, Y: box_depth},
        n,
        pattern,
    )
    side_y = Term("{bottom}.y", box_y.value)
    depth = Term("{bottom}.width", box_depth.value)
    left = _part(
        "left",
        "Zarge_links",
        Orientation.YZ,
        s.box_material_id,
        ts,
        {X: Term("{bottom}.x", box_x.value), Y: side_y, Z: on_bottom},
        {Y: depth, Z: side_height},
        n,
        pattern,
    )
    right_x = box_x.value + box_width.value - box_thickness
    right = _part(
        "right",
        "Zarge_rechts",
        Orientation.YZ,
        s.box_material_id,
        ts,
        {X: Term("{bottom}.x2 - {right}.thickness", right_x), Y: side_y, Z: on_bottom},
        {Y: depth, Z: side_height},
        n,
        pattern,
    )
    inner_width = Term("{right}.x - {left}.x2", box_width.value - 2 * box_thickness)
    piece_height = Term("{left}.width", side_height.value)
    piece_x = Term("{left}.x2", box_x.value + box_thickness)
    front_piece = _part(
        "fpiece",
        "Zarge_vorne",
        Orientation.XZ,
        s.box_material_id,
        ts,
        {X: piece_x, Y: side_y, Z: on_bottom},
        {X: inner_width, Z: piece_height},
        n,
        pattern,
    )
    back_piece = _part(
        "bpiece",
        "Zarge_hinten",
        Orientation.XZ,
        s.box_material_id,
        ts,
        {
            X: piece_x,
            Y: Term(
                "{bottom}.y2 - {bpiece}.thickness",
                box_y.value + box_depth.value - box_thickness,
            ),
            Z: on_bottom,
        },
        {X: inner_width, Z: piece_height},
        n,
        pattern,
    )
    parts = [front, bottom, left, right, front_piece, back_piece]
    slides = [
        (FittingKind.SLIDE, "left", op.sides[-1]),
        (FittingKind.SLIDE, "right", op.sides[1]),
    ]
    return FrontPlan(FrontKind.DRAWER, parts, list(op.board_ids), slides)


# ====================================================================== create
def create(doc: Document, plan: FrontPlan, group_id: str | None) -> list[str]:
    """Add the planned parts to ``doc`` (a drawer in a group of its own);
    returns their ids."""
    if plan.kind == FrontKind.DRAWER:
        name = doc.unique_name("Schublade", is_parameter=False)
        group_id = doc.add_group(name, group_id).id
    names: dict[str, str] = {}
    for part in plan.parts:
        names[part.key] = doc.unique_name(part.base_name, is_parameter=False)

    def fill(formula: str) -> str:
        for key, name in names.items():
            formula = formula.replace("{" + key + "}", name)
        return formula

    ids = []
    for part in plan.parts:
        board = doc.add_board(names[part.key], group_id, part.material_id, part.orientation)
        board.pattern_axis = part.pattern_axis
        doc.set_expression(board.id, "thickness", fill(part.thickness.formula))
        for prop in ("length", "width", "x", "y", "z", "count", "spacing"):
            if prop in part.expressions:
                doc.set_expression(board.id, prop, fill(part.expressions[prop]))
        ids.append(board.id)
    by_key = {part.key: board_id for part, board_id in zip(plan.parts, ids, strict=True)}
    for kind, key, carrier in plan.fittings:
        if carrier in doc.boards:
            doc.add_fitting(kind, by_key[key], carrier)
    return ids
