"""Fit new boards into a compartment ("Fach") between existing boards.

A compartment is found from a point inside it: from there we look along
each axis for the nearest board face. Every face carries a formula
(``Boden.z2``, ``Fachboden.z + 1 * Fachboden.spacing``), so a fitted board
is sized by formulas and keeps fitting when the furniture changes.

Only axis-aligned (not rotated) boards bound a compartment.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.part import Axis, Orientation

type Vec3 = tuple[float, float, float]
type Range = tuple[float, float]

AXES = (Axis.X, Axis.Y, Axis.Z)
PROBE = 1e-3  # half width of the "line" we look along
MIN_GAP = 1.0  # smallest compartment size in mm


@dataclass(frozen=True, slots=True)
class Face:
    value: float
    formula: str
    board_id: str
    touching: bool = True  # False: only the extent of a neighbour (open side)


@dataclass(frozen=True, slots=True)
class Box:
    board_id: str
    lo: Vec3
    hi: Vec3
    lo_formula: tuple[str, str, str]
    hi_formula: tuple[str, str, str]

    def face(self, axis: int, high: bool, touching: bool = True) -> Face:
        if high:
            return Face(self.hi[axis], self.hi_formula[axis], self.board_id, touching)
        return Face(self.lo[axis], self.lo_formula[axis], self.board_id, touching)


def boxes(doc: Document, evaluation: Evaluation, exclude: Iterable[str] = ()) -> list[Box]:
    """World boxes of every visible, not rotated board copy."""
    skip = set(exclude)
    result = []
    for board in doc.boards.values():
        if board.id in skip or not doc.is_visible(board):
            continue
        state = evaluation.boards.get(board.id)
        if state is None or state.rotated:
            continue
        size = state.size
        for i, origin in enumerate(state.instance_origins()):
            lo_f, hi_f = [], []
            for axis in AXES:
                suffix = ""
                if i and axis == state.pattern_axis:
                    suffix = f" + {i} * {board.name}.spacing"
                lo_f.append(f"{board.name}.{axis.value}{suffix}")
                hi_f.append(f"{board.name}.{axis.value}2{suffix}")
            hi = (origin[0] + size[0], origin[1] + size[1], origin[2] + size[2])
            result.append(
                Box(board.id, origin, hi, (lo_f[0], lo_f[1], lo_f[2]), (hi_f[0], hi_f[1], hi_f[2]))
            )
    return result


def _overlaps(box: Box, axis: int, interval: Range) -> bool:
    return min(box.hi[axis], interval[1]) - max(box.lo[axis], interval[0]) > 1e-6


def cast(
    found: list[Box], q: Vec3, axis: int, direction: int, ranges: dict[int, Range]
) -> Face | None:
    """The nearest face seen from ``q`` along ``axis`` in ``direction`` (±1), of
    a box that overlaps ``ranges`` on the other axes."""
    box = cast_box(found, q, axis, direction, ranges)
    return None if box is None else box.face(axis, high=direction < 0)


def cast_box(
    found: list[Box], q: Vec3, axis: int, direction: int, ranges: dict[int, Range]
) -> Box | None:
    """The box whose face :func:`cast` finds."""
    best: Box | None = None
    for box in found:
        if not all(_overlaps(box, k, r) for k, r in ranges.items()):
            continue
        if direction > 0:
            if box.lo[axis] < q[axis] - 1e-6:
                continue
            if best is None or box.lo[axis] < best.lo[axis]:
                best = box
        else:
            if box.hi[axis] > q[axis] + 1e-6:
                continue
            if best is None or box.hi[axis] > best.hi[axis]:
                best = box
    return best


def point_range(q: Vec3, axis: int) -> Range:
    return q[axis] - PROBE, q[axis] + PROBE


@dataclass(slots=True)
class Compartment:
    lo: list[Face]  # per world axis 0, 1, 2
    hi: list[Face]

    def size(self, axis: int) -> float:
        return self.hi[axis].value - self.lo[axis].value

    def board_ids(self) -> set[str]:
        return {f.board_id for f in (*self.lo, *self.hi) if f.touching}


def _fill_open_sides(
    found: list[Box], lo: list[Face | None], hi: list[Face | None]
) -> Compartment | None:
    """Open sides take the extent of the boards around (e.g. an open front:
    as deep as the side walls)."""
    neighbours = {f.board_id for f in (*lo, *hi) if f is not None}
    near = [b for b in found if b.board_id in neighbours]
    for axis in range(3):
        if lo[axis] is None and near:
            box = min(near, key=lambda b: b.lo[axis])
            lo[axis] = box.face(axis, high=False, touching=False)
        if hi[axis] is None and near:
            box = max(near, key=lambda b: b.hi[axis])
            hi[axis] = box.face(axis, high=True, touching=False)
    if any(f is None for f in (*lo, *hi)):
        return None
    comp = Compartment([f for f in lo if f is not None], [f for f in hi if f is not None])
    if any(comp.size(axis) < MIN_GAP for axis in range(3)):
        return None
    return comp


def compartment(
    doc: Document,
    evaluation: Evaluation,
    point: Vec3,
    normal: int,
    sign: int,
    exclude: Iterable[str] = (),
) -> Compartment | None:
    """The compartment in front of a board face.

    ``point`` lies on a face whose outward normal is world axis ``normal``
    in direction ``sign`` (±1), e.g. the front face of a back wall.
    """
    found = boxes(doc, evaluation, exclude)
    shifted = list(point)
    shifted[normal] += sign * 0.5
    q = (shifted[0], shifted[1], shifted[2])
    a, b = (k for k in range(3) if k != normal)
    on_line = {a: point_range(q, a), b: point_range(q, b)}
    back = cast(found, q, normal, -sign, on_line)
    front = cast(found, q, normal, sign, on_line)
    near = back.value if back is not None else q[normal] - sign * 0.5
    far = front.value if front is not None else sign * math.inf
    depth = (min(near, far), max(near, far))

    lo: list[Face | None] = [None, None, None]
    hi: list[Face | None] = [None, None, None]
    for axis, other in ((a, b), (b, a)):
        ranges = {other: point_range(q, other), normal: depth}
        lo[axis] = cast(found, q, axis, -1, ranges)
        hi[axis] = cast(found, q, axis, 1, ranges)
    if sign > 0:
        lo[normal], hi[normal] = back, front
    else:
        lo[normal], hi[normal] = front, back
    return _fill_open_sides(found, lo, hi)


def ref(formula: str) -> str:
    """A formula that can be subtracted safely."""
    return formula if " " not in formula else f"({formula})"


def span(lo: Face, hi: Face) -> str:
    return f"{hi.formula} - {ref(lo.formula)}"


# ====================================================================== new boards
class InsertKind(StrEnum):
    DIVIDER = "divider"  # Trennwand: upright, thickness horizontal
    SHELF = "shelf"  # Fachboden: lying
    BACK = "back"  # Rückwand in Nut: upright, thickness along Y, at the back


BASE_NAMES = {
    InsertKind.DIVIDER: "Trennwand",
    InsertKind.SHELF: "Fachboden",
    InsertKind.BACK: "Rückwand",
}


@dataclass(slots=True)
class GrooveSettings:
    depth: float = 8.0  # groove depth in the surrounding parts
    play: float = 0.5
    distance: float = 10.0  # from the back edge of the furniture to the panel


@dataclass(slots=True)
class InsertPlan:
    """A board that fits into a compartment - for the preview and for creating it."""

    kind: InsertKind
    orientation: Orientation
    lo: Vec3  # world box (preview)
    size: Vec3
    thickness: float
    # Formulas; ``{name}`` is replaced by the new board's name.
    expressions: dict[str, str] = field(default_factory=dict)
    joints: list[tuple[JointKind, str]] = field(default_factory=list)  # (kind, main board)
    centred: bool = False
    gap: float = 0.0  # clear distance from the lower face along the thickness axis


def _orientation(length: int, width: int) -> Orientation:
    return Orientation("xyz"[length] + "xyz"[width])


def plan_insert(
    doc: Document,
    evaluation: Evaluation,
    comp: Compartment,
    kind: InsertKind,
    thickness_axis: int,
    cursor: float,
    thickness: float,
    snap: float = 0.0,
    typed: float | None = None,
    groove: GrooveSettings | None = None,
) -> InsertPlan | None:
    """Where a new board goes in ``comp``.

    ``thickness_axis``: for a divider the horizontal axis it is thick along
    (it faces the viewer). ``cursor``: cursor position along that axis; the
    board centres on it, snapping to the middle within ``snap`` mm. ``typed``:
    an exact clear distance from the lower face.
    """
    t = 2 if kind == InsertKind.SHELF else thickness_axis
    if kind == InsertKind.BACK:
        t = 1
    rest = [k for k in range(3) if k != t]
    if kind == InsertKind.DIVIDER:
        length = 2
        width = rest[0] if rest[1] == 2 else rest[1]
    else:
        length, width = sorted(rest, key=comp.size, reverse=True)
    plan = InsertPlan(kind, _orientation(length, width), (0, 0, 0), (0, 0, 0), thickness)
    if comp.size(t) < thickness + (0 if kind == InsertKind.BACK else 0.1):
        return None

    lo_face, hi_face = comp.lo[t], comp.hi[t]
    own = "{name}.thickness"
    if kind == InsertKind.BACK:
        settings = groove or GrooveSettings()
        start = hi_face.value - settings.distance - thickness
        if start < lo_face.value:
            return None
        position = f"{hi_face.formula} - {settings.distance:g} - {own}"
    else:
        middle = lo_face.value + (comp.size(t) - thickness) / 2
        if typed is not None:
            start = lo_face.value + typed
        else:
            start = cursor - thickness / 2
            start = middle if abs(start - middle) <= snap else float(round(start))
        start = min(max(start, lo_face.value), hi_face.value - thickness)
        if typed is None and abs(start - middle) <= max(snap, 1e-6):
            plan.centred = True
            position = f"({lo_face.formula} + {hi_face.formula} - {own}) / 2"
        else:
            gap = round(start - lo_face.value, 3)
            position = lo_face.formula if abs(gap) < 1e-9 else f"{lo_face.formula} + {gap:g}"
        plan.gap = start - lo_face.value

    lo = [0.0, 0.0, 0.0]
    size = [0.0, 0.0, 0.0]
    lo[t], size[t] = start, thickness
    plan.expressions["xyz"[t]] = position
    for axis, dim in ((length, "length"), (width, "width")):
        a, b = comp.lo[axis], comp.hi[axis]
        lo[axis], size[axis] = a.value, b.value - a.value
        plan.expressions["xyz"[axis]] = a.formula
        plan.expressions[dim] = span(a, b)
    plan.lo = (lo[0], lo[1], lo[2])
    plan.size = (size[0], size[1], size[2])

    if kind == InsertKind.BACK:
        joint_axes = [length, width]
        joint_kind = JointKind.GROOVE
    else:
        joint_axes = [length]
        joint_kind = JointKind.DOWEL
    for axis in joint_axes:
        for face in (comp.lo[axis], comp.hi[axis]):
            if face.touching and (joint_kind, face.board_id) not in plan.joints:
                plan.joints.append((joint_kind, face.board_id))
    return plan


def back_compartment(
    doc: Document,
    evaluation: Evaluation,
    comp: Compartment,
    point: Vec3,
    thickness: float,
    groove: GrooveSettings,
) -> Compartment | None:
    """For a back panel: the compartment at the back, between the parts the
    panel itself would meet (shelves standing in front of it do not count)."""
    found = boxes(doc, evaluation)
    rear = comp.hi[1]
    slab = (rear.value - groove.distance - thickness, rear.value - groove.distance)
    q = (point[0], (slab[0] + slab[1]) / 2, point[2])
    lo: list[Face | None] = [None, comp.lo[1], None]
    hi: list[Face | None] = [None, rear, None]
    for axis, other in ((0, 2), (2, 0)):
        ranges = {other: point_range(q, other), 1: slab}
        lo[axis] = cast(found, q, axis, -1, ranges)
        hi[axis] = cast(found, q, axis, 1, ranges)
    return _fill_open_sides(found, lo, hi)


def create(
    doc: Document,
    plan: InsertPlan,
    group_id: str | None,
    material_id: str,
    thickness_formula: str,
    groove: GrooveSettings | None = None,
) -> str:
    """Add the planned board (with its joints) to ``doc``; returns its id."""
    name = doc.unique_name(BASE_NAMES[plan.kind], is_parameter=False)
    board = doc.add_board(name, group_id, material_id, plan.orientation)
    doc.set_expression(board.id, "thickness", thickness_formula)
    for prop in ("length", "width", "x", "y", "z"):
        doc.set_expression(board.id, prop, plan.expressions[prop].replace("{name}", name))
    board.back_panel = plan.kind == InsertKind.BACK
    settings = groove or GrooveSettings()
    for kind, main_id in plan.joints:
        joint = doc.add_joint(kind, main_id, board.id)
        if kind == JointKind.GROOVE:
            doc.set_joint_param(joint.id, "depth", settings.depth)
            doc.set_joint_param(joint.id, "play", settings.play)
    return board.id


# ====================================================================== back panel in groove
def seat_in_groove(
    doc: Document, evaluation: Evaluation, panel_id: str, groove: GrooveSettings
) -> list[str]:
    """Fit a panel between the parts around it, set it ``distance`` from the
    back edge and join it with grooves. Returns the surrounding board ids.

    Raises ValueError when the panel is not surrounded on all four edges.
    """
    state = evaluation.boards[panel_id]
    if state.rotated or state.count != 1:
        raise ValueError("panel must not be rotated or repeated")
    t = state.orientation.thickness_axis.position
    lo_t = state.origin[t]
    slab = (lo_t, lo_t + state.extent(AXES[t]))
    size = state.size
    q = (
        state.origin[0] + size[0] / 2,
        state.origin[1] + size[1] / 2,
        state.origin[2] + size[2] / 2,
    )
    found = boxes(doc, evaluation, exclude=[panel_id])
    faces: dict[int, tuple[Face, Face]] = {}
    for axis in (k for k in range(3) if k != t):
        (other,) = (k for k in range(3) if k not in (t, axis))
        ranges = {other: point_range(q, other), t: slab}
        a = cast(found, q, axis, -1, ranges)
        b = cast(found, q, axis, 1, ranges)
        if a is None or b is None:
            raise ValueError("panel is not surrounded by parts")
        faces[axis] = (a, b)
    neighbours = {f.board_id for pair in faces.values() for f in pair}
    near = [box for box in found if box.board_id in neighbours]
    back_lo = min(near, key=lambda box: box.lo[t]).face(t, high=False)
    back_hi = max(near, key=lambda box: box.hi[t]).face(t, high=True)

    name = doc.boards[panel_id].name
    for axis, (a, b) in faces.items():
        dim = state.orientation.dimension_along(AXES[axis])
        doc.set_expression(panel_id, "xyz"[axis], a.formula)
        doc.set_expression(panel_id, dim, span(a, b))
    distance = f"{groove.distance:g}"
    if back_hi.value - (q[t]) <= q[t] - back_lo.value:
        position = f"{back_hi.formula} - {distance} - {name}.thickness"
        doc.boards[panel_id].back_panel = True
    else:
        position = f"{back_lo.formula} + {distance}"
    doc.set_expression(panel_id, "xyz"[t], position)

    for joint in list(doc.joints.values()):
        if panel_id in (joint.main_id, joint.attached_id) and (
            joint.main_id in neighbours or joint.attached_id in neighbours
        ):
            del doc.joints[joint.id]
    for main_id in sorted(neighbours, key=lambda i: doc.boards[i].name):
        joint = doc.add_joint(JointKind.GROOVE, main_id, panel_id)
        doc.set_joint_param(joint.id, "depth", groove.depth)
        doc.set_joint_param(joint.id, "play", groove.play)
    return sorted(neighbours)
