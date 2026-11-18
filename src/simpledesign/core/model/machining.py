"""Machining (Bearbeitungen) of all parts, computed from the joints.

For every pattern copy of every board this gives a :class:`PartShape`: the
*rough* box (visible size plus joint extensions) and the features cut into it
(holes, box cut-outs, prisms for dovetails). All coordinates are world-axis
aligned; feature coordinates are relative to the rough box's min corner, so
identical copies share a mesh.

Contact between two parts is found from the visible boxes: the attached part
must touch a face of the main part with an overlapping area. A joint that
cannot be built (no contact, too deep …) gets an error and changes nothing -
the model stays valid, as with formula errors.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from simpledesign.core.model.document import Document
from simpledesign.core.model.joint import JOINT_DEFAULTS, Joint, JointKind
from simpledesign.core.model.part import Orientation
from simpledesign.core.model.state import BoardState, Vec3

TOLERANCE = 0.01  # mm: faces closer than this touch
_ROUND = 2  # decimals for comparing machining of different parts


class JointErrorCode(StrEnum):
    NO_CONTACT = "no_contact"  # the parts do not touch
    FACE_CONTACT = "face_contact"  # needs an edge of the attached part, not its face
    MAIN_EDGE = "main_edge"  # needs the face of the main part, not its edge (corners)
    TOO_DEEP = "too_deep"  # hole/groove deeper than the part
    SCREW_TOO_SHORT = "screw_too_short"
    INVALID_VALUE = "invalid_value"  # a setting is out of range


@dataclass(frozen=True, slots=True)
class Hole:
    center: Vec3  # where drilling starts (on the surface), relative to the part
    axis: int  # 0, 1, 2 = x, y, z
    direction: int  # +1 / -1: drilling direction along the axis
    diameter: float
    depth: float


@dataclass(frozen=True, slots=True)
class Cutout:
    """A box of material removed (groove, rabbet, pocket)."""

    lo: Vec3
    hi: Vec3


@dataclass(frozen=True, slots=True)
class Prism:
    """Material removed by a polygon swept along an axis (dovetails).

    ``points`` lie on a plane across ``axis``; the sweep goes ``length`` in +axis.
    """

    points: tuple[Vec3, ...]
    axis: int
    length: float


type Feature = Hole | Cutout | Prism


@dataclass(frozen=True, slots=True)
class PartShape:
    origin: Vec3  # world min corner of the rough box
    size: Vec3  # rough size, world axes
    features: tuple[Feature, ...] = ()
    cut_operations: int = 0  # grooves, mortises, tenons … (each may have several features)


@dataclass(frozen=True, slots=True)
class Hardware:
    """Fittings for the bill of materials (Beschläge)."""

    kind: JointKind
    diameter: float
    length: float
    quantity: int


@dataclass(slots=True)
class Machining:
    parts: dict[str, list[PartShape]] = field(default_factory=dict)
    hardware: list[Hardware] = field(default_factory=list)
    errors: dict[str, JointErrorCode] = field(default_factory=dict)


# ====================================================================== internals
class _PlanError(Exception):
    def __init__(self, code: JointErrorCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class _Instance:
    lo: list[float]  # visible box
    hi: list[float]
    ext_lo: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    ext_hi: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    features: list[Feature] = field(default_factory=list)  # world coordinates
    cut_operations: int = 0

    def extent(self, axis: int) -> float:
        return self.hi[axis] - self.lo[axis]

    def shape(self) -> PartShape:
        lo = [self.lo[i] - self.ext_lo[i] for i in range(3)]
        hi = [self.hi[i] + self.ext_hi[i] for i in range(3)]
        features = sorted((_relative(f, lo) for f in self.features), key=repr)
        size = _vec([hi[i] - lo[i] for i in range(3)])
        return PartShape(_vec(lo), size, tuple(features), self.cut_operations)


@dataclass(frozen=True, slots=True)
class _Contact:
    axis: int
    side: int  # +1: the attached part lies on the + side of the main part
    lo: Vec3  # overlap rectangle on the contact plane (lo[axis] == hi[axis])
    hi: Vec3

    @property
    def plane(self) -> float:
        return self.lo[self.axis]


def _vec(values: list[float]) -> Vec3:
    return (values[0], values[1], values[2])


def _relative(feature: Feature, origin: list[float]) -> Feature:
    def shift(p: Vec3) -> Vec3:
        return _vec([round(p[i] - origin[i], 6) for i in range(3)])

    if isinstance(feature, Hole):
        return Hole(
            shift(feature.center),
            feature.axis,
            feature.direction,
            feature.diameter,
            feature.depth,
        )
    if isinstance(feature, Prism):
        points = tuple(shift(p) for p in feature.points)
        return Prism(points, feature.axis, feature.length)
    return Cutout(shift(feature.lo), shift(feature.hi))


def _contact(main: _Instance, attached: _Instance) -> _Contact | None:
    for k in range(3):
        if abs(attached.lo[k] - main.hi[k]) < TOLERANCE:
            side, plane = 1, main.hi[k]
        elif abs(attached.hi[k] - main.lo[k]) < TOLERANCE:
            side, plane = -1, main.lo[k]
        else:
            continue
        lo = [max(main.lo[i], attached.lo[i]) for i in range(3)]
        hi = [min(main.hi[i], attached.hi[i]) for i in range(3)]
        if all(hi[i] - lo[i] > TOLERANCE for i in range(3) if i != k):
            lo[k] = hi[k] = plane
            return _Contact(k, side, _vec(lo), _vec(hi))
    return None


def _check_params(joint: Joint) -> None:
    p = {name: joint.param(name) for name in JOINT_DEFAULTS[joint.kind]}
    if any(not math.isfinite(v) or v < 0 for v in p.values()):
        raise _PlanError(JointErrorCode.INVALID_VALUE)
    positive = ("diameter", "length", "max_spacing", "depth", "slope", "pin_width")
    if any(p.get(name, 1.0) <= 0 for name in positive):
        raise _PlanError(JointErrorCode.INVALID_VALUE)
    if joint.kind == JointKind.DOWEL and p["depth_main"] >= p["length"]:
        raise _PlanError(JointErrorCode.INVALID_VALUE)
    if joint.kind in (JointKind.GROOVE, JointKind.RABBET) and p["play"] >= p["depth"]:
        raise _PlanError(JointErrorCode.INVALID_VALUE)


def hole_positions(
    length: float, edge_distance: float, max_spacing: float, count: int
) -> list[float]:
    """Positions along a joint line of ``length`` (drilling pattern rules).

    ``count`` 0 = automatic: holes ``edge_distance`` from both ends and at
    most ``max_spacing`` apart, at least two if there is room.
    """
    usable = length - 2 * edge_distance
    if count <= 0:
        count = 1 if usable <= 0 else max(2, math.ceil(usable / max_spacing - 1e-9) + 1)
    if count == 1:
        return [length / 2]
    margin = edge_distance if usable > 0 else length / (2 * count)
    step = (length - 2 * margin) / (count - 1)
    return [margin + i * step for i in range(count)]


def _hole_points(joint: Joint, contact: _Contact) -> list[Vec3]:
    """Hole centres on the contact plane: in a row along the longer side, centred across."""
    u, v = (i for i in range(3) if i != contact.axis)
    long, short = (
        (u, v) if contact.hi[u] - contact.lo[u] >= contact.hi[v] - contact.lo[v] else (v, u)
    )
    positions = hole_positions(
        contact.hi[long] - contact.lo[long],
        joint.param("edge_distance"),
        joint.param("max_spacing"),
        round(joint.param("count")),
    )
    points = []
    for pos in positions:
        point = [0.0, 0.0, 0.0]
        point[contact.axis] = contact.plane
        point[long] = contact.lo[long] + pos
        point[short] = (contact.lo[short] + contact.hi[short]) / 2
        points.append(_vec(point))
    return points


@dataclass(frozen=True, slots=True)
class _Extension:
    """The attached part grows ``length`` into the main part (rough size)."""

    axis: int
    side: int  # contact side: the part grows towards -side
    length: float


# A hole, a group of features that form one operation (groove, tenon …), or an extension.
type _Op = tuple[_Instance, Hole | tuple[Feature, ...] | _Extension]


def _span(lo: list[float], hi: list[float], axis: int, a: float, b: float) -> None:
    lo[axis], hi[axis] = min(a, b), max(a, b)


def _box_minus(
    outer: tuple[list[float], list[float]], inner: tuple[list[float], list[float]]
) -> list[Cutout]:
    """``outer`` minus ``inner`` (a box inside it) as up to six boxes."""
    lo, hi = list(outer[0]), list(outer[1])
    pieces = []
    for i in range(3):
        if inner[0][i] - lo[i] > TOLERANCE:
            top = list(hi)
            top[i] = inner[0][i]
            pieces.append(Cutout(_vec(lo), _vec(top)))
        if hi[i] - inner[1][i] > TOLERANCE:
            bottom = list(lo)
            bottom[i] = inner[1][i]
            pieces.append(Cutout(_vec(bottom), _vec(hi)))
        lo[i], hi[i] = max(lo[i], inner[0][i]), min(hi[i], inner[1][i])
    return pieces


def _plan(
    joint: Joint,
    main: _Instance,
    attached: _Instance,
    c: _Contact,
    main_thickness: int,
    attached_thickness: int,
) -> tuple[list[_Op], int]:
    """Operations for one contact and the number of fittings used."""
    if joint.kind in (JointKind.DOWEL, JointKind.SCREW):
        return _plan_holes(joint, main, attached, c)
    # All others take the attached part with an edge (its end or long edge).
    if c.axis == attached_thickness:
        raise _PlanError(JointErrorCode.FACE_CONTACT)
    if joint.kind in (JointKind.GROOVE, JointKind.RABBET):
        return _plan_groove(joint, main, attached, c, attached_thickness), 0
    if joint.kind == JointKind.TENON:
        return _plan_tenon(joint, main, attached, c, attached_thickness), 0
    # Corner joints go through the main part's thickness.
    if c.axis != main_thickness:
        raise _PlanError(JointErrorCode.MAIN_EDGE)
    return _plan_corner(joint, main, attached, c, attached_thickness), 0


def _plan_holes(
    joint: Joint, main: _Instance, attached: _Instance, c: _Contact
) -> tuple[list[_Op], int]:
    k, into_main = c.axis, -c.side
    diameter, length = joint.param("diameter"), joint.param("length")
    if joint.kind == JointKind.DOWEL:
        depth_main = joint.param("depth_main")
        depth_attached = length - depth_main + 1.0  # 1 mm room for glue
        hole_main = diameter
        pilot = diameter
    else:
        depth_main = main.extent(k)  # through the main part
        depth_attached = length - depth_main
        if depth_attached < 5.0:
            raise _PlanError(JointErrorCode.SCREW_TOO_SHORT)
        hole_main = diameter + 0.5  # clearance hole
        pilot = round(diameter * 0.7 * 2) / 2  # pilot hole, 0.5 mm steps
    if (joint.kind == JointKind.DOWEL and depth_main >= main.extent(k)) or (
        depth_attached >= attached.extent(k)
    ):
        raise _PlanError(JointErrorCode.TOO_DEEP)
    ops: list[_Op] = []
    points = _hole_points(joint, c)
    for point in points:
        ops.append((main, Hole(point, k, into_main, hole_main, depth_main)))
        ops.append((attached, Hole(point, k, -into_main, pilot, depth_attached)))
    return ops, len(points)


def _plan_groove(
    joint: Joint, main: _Instance, attached: _Instance, c: _Contact, t: int
) -> list[_Op]:
    """Groove / rabbet: the attached part goes into the main part with an edge."""
    k = c.axis
    depth, play = joint.param("depth"), joint.param("play")
    if depth >= main.extent(k) - TOLERANCE:
        raise _PlanError(JointErrorCode.TOO_DEEP)
    lo, hi = list(c.lo), list(c.hi)
    _span(lo, hi, k, c.plane, c.plane - c.side * depth)
    lo[t] -= play / 2
    hi[t] += play / 2
    if joint.kind == JointKind.RABBET:
        # Open towards the nearer outside face of the main part.
        if attached.lo[t] + attached.hi[t] >= main.lo[t] + main.hi[t]:
            hi[t] = main.hi[t]
        else:
            lo[t] = main.lo[t]
    lo = [max(lo[i], main.lo[i]) for i in range(3)]
    hi = [min(hi[i], main.hi[i]) for i in range(3)]
    return [
        (main, (Cutout(_vec(lo), _vec(hi)),)),
        (attached, _Extension(k, c.side, depth - play)),
    ]


def _plan_tenon(
    joint: Joint, main: _Instance, attached: _Instance, c: _Contact, t: int
) -> list[_Op]:
    """Blind tenon (Zapfen): centred in the thickness, shoulders at the sides."""
    k = c.axis
    w = 3 - k - t
    length, shoulder = joint.param("length"), joint.param("shoulder")
    thickness = joint.param("thickness") or attached.extent(t) / 3
    if thickness >= c.hi[t] - c.lo[t] - TOLERANCE or c.hi[w] - c.lo[w] - 2 * shoulder <= 1.0:
        raise _PlanError(JointErrorCode.INVALID_VALUE)
    depth = length + 1.0  # 1 mm room for glue at the bottom of the mortise
    if depth >= main.extent(k) - TOLERANCE:
        raise _PlanError(JointErrorCode.TOO_DEEP)
    middle = (c.lo[t] + c.hi[t]) / 2
    tenon_lo, tenon_hi = list(c.lo), list(c.hi)
    tenon_lo[t], tenon_hi[t] = middle - thickness / 2, middle + thickness / 2
    tenon_lo[w] += shoulder
    tenon_hi[w] -= shoulder
    mortise_lo, mortise_hi = list(tenon_lo), list(tenon_hi)
    _span(mortise_lo, mortise_hi, k, c.plane, c.plane - c.side * depth)
    _span(tenon_lo, tenon_hi, k, c.plane, c.plane - c.side * length)
    # Everything of the extension around the tenon is cut away again.
    region_lo, region_hi = list(attached.lo), list(attached.hi)
    _span(region_lo, region_hi, k, c.plane, c.plane - c.side * length)
    waste = _box_minus((region_lo, region_hi), (tenon_lo, tenon_hi))
    return [
        (main, (Cutout(_vec(mortise_lo), _vec(mortise_hi)),)),
        (attached, _Extension(k, c.side, length)),
        (attached, tuple(waste)),
    ]


def _finger_layout(width: float, depth: float, count: int) -> list[tuple[float, float]]:
    """Fingers of the attached part as (start, end) along the joint line.

    The main part keeps the first finger, the attached part the second …;
    ``count`` 0 = automatic: an odd number, fingers about as wide as the parts are thick.
    """
    if count <= 0:
        count = max(3, 2 * round((width / depth - 1) / 2) + 1)
    if count < 2:
        raise _PlanError(JointErrorCode.INVALID_VALUE)
    step = width / count
    return [(i * step, (i + 1) * step) for i in range(1, count, 2)]


def _plan_corner(
    joint: Joint, main: _Instance, attached: _Instance, c: _Contact, t: int
) -> list[_Op]:
    """Finger joint or through dovetail: the attached part reaches through the main part.

    Seen across ``t`` (the attached part's thickness) the joint is a strip of
    ``depth`` x ``width``. Each tail/finger of the attached part is given as its
    side positions at the shoulder (``s0``, ``s1``) and at the far face (``f0``, ``f1``).
    """
    k = c.axis
    w = 3 - k - t
    depth = main.extent(k)
    width = c.hi[w] - c.lo[w]
    near, far = c.plane, c.plane - c.side * depth
    count = round(joint.param("count"))
    tails: list[tuple[float, float, float, float]] = []
    if joint.kind == JointKind.FINGER:
        tails = [(a, b, a, b) for a, b in _finger_layout(width, depth, count)]
    else:
        count = count or max(1, round(width / (4 * depth)))
        pitch = width / count
        far_half = (pitch - joint.param("pin_width")) / 2
        near_half = far_half - depth / joint.param("slope")
        if near_half < 1.0:
            raise _PlanError(JointErrorCode.INVALID_VALUE)
        for i in range(count):
            centre = (i + 0.5) * pitch
            tails.append(
                (centre - near_half, centre + near_half, centre - far_half, centre + far_half)
            )
    origin = c.lo[w]

    def piece(s0: float, s1: float, f0: float, f1: float) -> Feature:
        s0, s1, f0, f1 = (origin + v for v in (s0, s1, f0, f1))
        if abs(s0 - f0) < 1e-9 and abs(s1 - f1) < 1e-9:
            lo, hi = list(c.lo), list(c.hi)
            _span(lo, hi, k, near, far)
            lo[w], hi[w] = s0, s1
            return Cutout(_vec(lo), _vec(hi))
        corners = []
        for kv, wv in ((near, s0), (near, s1), (far, f1), (far, f0)):
            point = [0.0, 0.0, 0.0]
            point[k], point[w], point[t] = kv, wv, c.lo[t]
            corners.append(_vec(point))
        return Prism(tuple(corners), t, c.hi[t] - c.lo[t])

    # Sockets in the main part = the tails; the gaps between them are cut from the attached part.
    sockets = tuple(piece(*tail) for tail in tails)
    gaps: list[Feature] = []
    previous = (0.0, 0.0)
    for s0, s1, f0, f1 in [*tails, (width, width, width, width)]:
        if s0 - previous[0] > TOLERANCE or f0 - previous[1] > TOLERANCE:
            gaps.append(piece(previous[0], s0, previous[1], f0))
        previous = (s1, f1)
    region_lo, region_hi = list(attached.lo), list(attached.hi)
    _span(region_lo, region_hi, k, near, far)
    strip_lo, strip_hi = list(c.lo), list(c.hi)
    _span(strip_lo, strip_hi, k, near, far)
    gaps += _box_minus((region_lo, region_hi), (strip_lo, strip_hi))
    return [
        (main, sockets),
        (attached, _Extension(k, c.side, depth)),
        (attached, tuple(gaps)),
    ]


def _apply(op: _Op) -> None:
    instance, what = op
    if isinstance(what, _Extension):
        # side > 0: the attached part lies above the main part -> grows downwards.
        target = instance.ext_lo if what.side > 0 else instance.ext_hi
        target[what.axis] = max(target[what.axis], what.length)
    elif isinstance(what, tuple):
        if what:
            instance.features += what
            instance.cut_operations += 1
    else:
        instance.features.append(what)


def compute_machining(doc: Document, boards: Mapping[str, BoardState]) -> Machining:
    instances = {
        board_id: [
            _Instance(list(origin), [origin[i] + state.size[i] for i in range(3)])
            for origin in state.instance_origins()
        ]
        for board_id, state in boards.items()
    }
    result = Machining()
    fittings: dict[tuple[JointKind, float, float], int] = {}
    for joint in doc.joints.values():
        if joint.main_id not in boards or joint.attached_id not in boards:
            continue
        main_thickness = boards[joint.main_id].orientation.thickness_axis.position
        attached_thickness = boards[joint.attached_id].orientation.thickness_axis.position
        try:
            _check_params(joint)
            ops: list[_Op] = []
            count = 0
            for main in instances[joint.main_id]:
                for attached in instances[joint.attached_id]:
                    contact = _contact(main, attached)
                    if contact is not None:
                        contact_ops, n = _plan(
                            joint, main, attached, contact, main_thickness, attached_thickness
                        )
                        ops += contact_ops
                        count += n
            if not ops:
                raise _PlanError(JointErrorCode.NO_CONTACT)
        except _PlanError as failure:
            result.errors[joint.id] = failure.code
            continue
        for op in ops:
            _apply(op)
        if count:
            key = (joint.kind, joint.param("diameter"), joint.param("length"))
            fittings[key] = fittings.get(key, 0) + count
    result.parts = {board_id: [i.shape() for i in items] for board_id, items in instances.items()}
    result.hardware = [
        Hardware(kind, diameter, length, quantity)
        for (kind, diameter, length), quantity in sorted(fittings.items())
    ]
    return result


# ====================================================================== cut list helpers
type Signature = tuple[object, ...]


def local_shape(shape: PartShape, orientation: Orientation) -> tuple[Vec3, Signature]:
    """Rough size as (length, width, thickness) and the features in the board's
    own frame - equal for parts that are machined the same, wherever they are."""
    perm = (
        orientation.length_axis.position,
        orientation.width_axis.position,
        orientation.thickness_axis.position,
    )

    def local(p: Vec3) -> Vec3:
        return _vec([round(p[i], _ROUND) for i in perm])

    items: list[tuple[object, ...]] = []
    for f in shape.features:
        if isinstance(f, Hole):
            items.append(
                ("hole", local(f.center), perm.index(f.axis), f.direction, f.diameter, f.depth)
            )
        elif isinstance(f, Prism):
            points = tuple(sorted(local(p) for p in f.points))
            items.append(("prism", perm.index(f.axis), points, round(f.length, _ROUND)))
        else:
            items.append(("cutout", local(f.lo), local(f.hi)))
    return local(shape.size), tuple(sorted(items, key=repr))
