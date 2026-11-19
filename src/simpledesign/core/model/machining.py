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

from simpledesign.core.model import fitting as fittings
from simpledesign.core.model.document import Document
from simpledesign.core.model.fitting import Fitting, FittingKind
from simpledesign.core.model.joint import JOINT_DEFAULTS, Joint, JointKind
from simpledesign.core.model.part import Orientation
from simpledesign.core.model.state import BoardState, Vec3
from simpledesign.core.sketch.profile import Region

TOLERANCE = 0.01  # mm: faces closer than this touch
_ROUND = 2  # decimals for comparing machining of different parts


class JointErrorCode(StrEnum):
    NO_CONTACT = "no_contact"  # the parts do not touch
    FACE_CONTACT = "face_contact"  # needs an edge of the attached part, not its face
    MAIN_EDGE = "main_edge"  # needs the face of the main part, not its edge (corners)
    TOO_DEEP = "too_deep"  # hole/groove deeper than the part
    SCREW_TOO_SHORT = "screw_too_short"
    INVALID_VALUE = "invalid_value"  # a setting is out of range
    ROTATED = "rotated"  # freely rotated parts get no automatic joints
    ORIENTATION = "orientation"  # fitting: the parts do not lie the right way


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


@dataclass(frozen=True, slots=True)
class Extrusion:
    """Sketch regions extruded along an axis.

    A plane point ``(u, v)`` lies at ``origin + u * e[axes[0]] + v * e[axes[1]]``;
    the extrusion runs ``length`` along +``axes[2]``. ``cut=False`` is the
    outline of a profile part (replaces the box), ``cut=True`` removes material.
    """

    regions: tuple[Region, ...]
    axes: tuple[int, int, int]
    origin: Vec3
    length: float
    cut: bool = True


type Feature = Hole | Cutout | Prism | Extrusion


@dataclass(frozen=True, slots=True)
class SketchFeature:
    """Input for :func:`compute_machining`: a solved sketch of a board."""

    regions: tuple[Region, ...]
    cut: bool = True
    # Cuts: from the top (thickness side) or bottom face, depth; None = through.
    top: bool = True
    depth: float | None = None


@dataclass(frozen=True, slots=True)
class PartShape:
    origin: Vec3  # world min corner of the rough box
    size: Vec3  # rough size, world axes
    features: tuple[Feature, ...] = ()
    cut_operations: int = 0  # grooves, mortises, tenons … (each may have several features)
    # Free rotation: (world axis 0/1/2, degrees) about ``origin``.
    rotation: tuple[int, float] | None = None


@dataclass(frozen=True, slots=True)
class Hardware:
    """Fittings for the bill of materials (Beschläge): joint fittings
    (dowels, screws) and fittings with a drilling pattern (hinges, slides)."""

    kind: JointKind | FittingKind
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
    if isinstance(feature, Extrusion):
        return Extrusion(
            feature.regions, feature.axes, shift(feature.origin), feature.length, feature.cut
        )
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


@dataclass(frozen=True, slots=True)
class _GrooveSpan:
    """After all extensions: the groove ``cutout`` must hold the attached part
    over its full rough length along ``axis`` (it may reach into other grooves,
    e.g. a back panel into the grooves of bottom and top)."""

    cutout: Cutout
    attached: _Instance
    axis: int


# A hole, a group of features that form one operation (groove, tenon …), an
# extension, or a groove to widen later.
type _Op = tuple[_Instance, Hole | tuple[Feature, ...] | _Extension | _GrooveSpan]


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
    cutout = Cutout(_vec(lo), _vec(hi))
    along = 3 - k - t  # the groove's length direction
    return [
        (main, (cutout,)),
        (attached, _Extension(k, c.side, depth - play)),
        (main, _GrooveSpan(cutout, attached, along)),
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


def _widen(main: _Instance, span: _GrooveSpan) -> None:
    """Let the groove run as far as the attached part's rough size (inside main)."""
    i, a = span.axis, span.attached
    lo, hi = list(span.cutout.lo), list(span.cutout.hi)
    lo[i] = max(min(lo[i], a.lo[i] - a.ext_lo[i]), main.lo[i])
    hi[i] = min(max(hi[i], a.hi[i] + a.ext_hi[i]), main.hi[i])
    wide = Cutout(_vec(lo), _vec(hi))
    if wide != span.cutout and span.cutout in main.features:
        main.features[main.features.index(span.cutout)] = wide


def _apply(op: _Op) -> None:
    instance, what = op
    if isinstance(what, _GrooveSpan):
        return  # after all joints, see _widen
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


# ====================================================================== fittings
X, Y, Z = 0, 1, 2
_HINGE_REACH = 100.0  # mm: the door edge lies at most this far from the carrier
_SLIDE_REACH = 50.0  # mm: gap between drawer side and carcass side
_PLATE_PITCH = 16.0  # mounting plate holes: 32 mm apart, around the cup height

type _FittingHole = tuple[_Instance, Hole]
type _HardwareKey = tuple[JointKind | FittingKind, float, float]


def _centre(instance: _Instance, axis: int) -> float:
    return (instance.lo[axis] + instance.hi[axis]) / 2


def _overlaps(a: _Instance, b: _Instance, axis: int) -> bool:
    return min(a.hi[axis], b.hi[axis]) - max(a.lo[axis], b.lo[axis]) > TOLERANCE


def _point(**coords: float) -> Vec3:
    return (coords["x"], coords["y"], coords["z"])


def _at(axis: int, plane: float, u: tuple[int, float], v: tuple[int, float]) -> Vec3:
    p = [0.0, 0.0, 0.0]
    p[axis], p[u[0]], p[v[0]] = plane, u[1], v[1]
    return _vec(p)


def _check_fitting_params(fitting: Fitting) -> None:
    values = [fitting.param(n) for n in fittings.FITTING_DEFAULTS[fitting.kind]]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise _PlanError(JointErrorCode.INVALID_VALUE)
    positive = (
        "cup_diameter",
        "cup_depth",
        "plate_hole_diameter",
        "plate_hole_depth",
        "hole_diameter",
        "hole_depth",
        "diameter",
        "depth",
        "spacing",
    )
    defaults = fittings.FITTING_DEFAULTS[fitting.kind]
    if any(fitting.param(n) <= 0 for n in positive if n in defaults):
        raise _PlanError(JointErrorCode.INVALID_VALUE)


def _plan_hinge(
    f: Fitting, doors: list[_Instance], carriers: list[_Instance]
) -> tuple[list[_FittingHole], int]:
    pairs = []
    for c in carriers:
        cx = _centre(c, X)
        for d in doors:
            if not _overlaps(c, d, Z) or d.hi[Y] < c.lo[Y] - TOLERANCE or d.lo[Y] > c.hi[Y]:
                continue
            distance = min(abs(d.lo[X] - cx), abs(d.hi[X] - cx))
            if distance <= _HINGE_REACH:
                pairs.append((distance, c, d))
    if not pairs:
        raise _PlanError(JointErrorCode.NO_CONTACT)
    nearest = min(p[0] for p in pairs)
    holes: list[_FittingHole] = []
    cups = 0
    for distance, c, d in pairs:
        if distance > nearest + 1.0:
            continue  # the other leaf of a pair of doors
        cx = _centre(c, X)
        left = cx < _centre(d, X)  # carrier left of the door: hinged at the door's left edge
        cup_x = d.lo[X] + f.param("cup_distance") if left else d.hi[X] - f.param("cup_distance")
        behind = _centre(c, Y) > _centre(d, Y)  # cup on the door face towards the carrier
        cup_y, cup_dir = (d.hi[Y], -1) if behind else (d.lo[Y], 1)
        if f.param("cup_depth") >= d.extent(Y) - TOLERANCE:
            raise _PlanError(JointErrorCode.TOO_DEEP)
        height, end = d.extent(Z), f.param("end_distance")
        count = round(f.param("count")) or fittings.hinge_count(height)
        if count == 1:
            positions = [height / 2]
        else:
            if height - 2 * end <= 0:
                raise _PlanError(JointErrorCode.INVALID_VALUE)
            step = (height - 2 * end) / (count - 1)
            positions = [end + i * step for i in range(count)]
        plate_x, plate_dir = (c.hi[X], -1) if left else (c.lo[X], 1)
        plate_y = max(c.lo[Y], min(d.hi[Y], c.hi[Y])) + f.param("plate_distance")
        if f.param("plate_hole_depth") >= c.extent(X) - TOLERANCE:
            raise _PlanError(JointErrorCode.TOO_DEEP)
        for pos in positions:
            z = d.lo[Z] + pos
            cup = _point(x=cup_x, y=cup_y, z=z)
            holes.append((d, Hole(cup, Y, cup_dir, f.param("cup_diameter"), f.param("cup_depth"))))
            for dz in (-_PLATE_PITCH, _PLATE_PITCH):
                if not (c.lo[Z] < z + dz < c.hi[Z] and c.lo[Y] < plate_y < c.hi[Y]):
                    raise _PlanError(JointErrorCode.NO_CONTACT)
                hole = Hole(
                    _point(x=plate_x, y=plate_y, z=z + dz),
                    X,
                    plate_dir,
                    f.param("plate_hole_diameter"),
                    f.param("plate_hole_depth"),
                )
                holes.append((c, hole))
            cups += 1
    return holes, cups


def _plan_slide(
    f: Fitting, parts: list[_Instance], carriers: list[_Instance]
) -> tuple[list[_FittingHole], dict[float, int]]:
    holes: list[_FittingHole] = []
    lengths: dict[float, int] = {}
    for p in parts:
        best: tuple[float, _Instance] | None = None
        for c in carriers:
            if not (_overlaps(p, c, Y) and _overlaps(p, c, Z)):
                continue
            gap = p.lo[X] - c.hi[X] if _centre(c, X) < _centre(p, X) else c.lo[X] - p.hi[X]
            if -TOLERANCE <= gap <= _SLIDE_REACH and (best is None or gap < best[0]):
                best = (gap, c)
        if best is None:
            continue
        c = best[1]
        depth = p.extent(Y)
        length = f.param("length") or fittings.slide_length(depth)
        if length is None or length > depth + TOLERANCE:
            raise _PlanError(JointErrorCode.INVALID_VALUE)
        first = f.param("front_distance")
        steps = math.floor((length - 40.0 - first) / fittings.SYSTEM_32 + 1e-9)
        if steps < 1:
            raise _PlanError(JointErrorCode.INVALID_VALUE)
        offsets = sorted(
            {first, first + steps // 2 * fittings.SYSTEM_32, first + steps * fittings.SYSTEM_32}
        )
        if f.param("hole_depth") >= c.extent(X) - TOLERANCE or (
            f.param("part_hole_depth") >= p.extent(X) - TOLERANCE
        ):
            raise _PlanError(JointErrorCode.TOO_DEEP)
        carrier_left = _centre(c, X) < _centre(p, X)
        c_plane, c_dir = (c.hi[X], -1) if carrier_left else (c.lo[X], 1)
        p_plane, p_dir = (p.lo[X], 1) if carrier_left else (p.hi[X], -1)
        z = _centre(p, Z)
        for offset in offsets:
            y = p.lo[Y] + offset
            if not c.lo[Y] < y < c.hi[Y]:
                raise _PlanError(JointErrorCode.NO_CONTACT)
            holes.append(
                (
                    c,
                    Hole(
                        _point(x=c_plane, y=y, z=z),
                        X,
                        c_dir,
                        f.param("hole_diameter"),
                        f.param("hole_depth"),
                    ),
                )
            )
            holes.append(
                (
                    p,
                    Hole(
                        _point(x=p_plane, y=y, z=z),
                        X,
                        p_dir,
                        f.param("part_hole_diameter"),
                        f.param("part_hole_depth"),
                    ),
                )
            )
        lengths[length] = lengths.get(length, 0) + 1
    if not lengths:
        raise _PlanError(JointErrorCode.NO_CONTACT)
    return holes, lengths


def _plan_shelf_pins(
    f: Fitting, parts: list[_Instance], thickness_axis: int, others: list[_Instance]
) -> list[_FittingHole]:
    k = thickness_axis
    if k == Z:
        raise _PlanError(JointErrorCode.ORIENTATION)
    w = 3 - k - Z  # across the part: front to back (or left to right)
    d, depth = f.param("diameter"), f.param("depth")
    r = d / 2
    holes: list[_FittingHole] = []
    for p in parts:
        if depth >= p.extent(k) - TOLERANCE:
            raise _PlanError(JointErrorCode.TOO_DEEP)
        rows = sorted({p.lo[w] + f.param("front_distance"), p.hi[w] - f.param("back_distance")})
        if rows[0] - r < p.lo[w] or rows[-1] + r > p.hi[w]:
            raise _PlanError(JointErrorCode.INVALID_VALUE)
        if len(rows) == 2 and rows[1] - rows[0] < d:
            rows = [(rows[0] + rows[1]) / 2]
        top = p.hi[Z] - f.param("last")
        z = p.lo[Z] + f.param("first")
        heights = []
        while z <= top + 1e-6:
            heights.append(z)
            z += f.param("spacing")
        if not heights:
            raise _PlanError(JointErrorCode.INVALID_VALUE)
        plane = p.hi[k] if f.side > 0 else p.lo[k]
        touching = [
            o
            for o in others
            if o is not p
            and abs((o.lo[k] if f.side > 0 else o.hi[k]) - plane) < TOLERANCE
            and _overlaps(o, p, w)
            and _overlaps(o, p, Z)
        ]
        for row in rows:
            for z in heights:
                covered = any(
                    o.lo[w] < row + r and o.hi[w] > row - r and o.lo[Z] < z + r and o.hi[Z] > z - r
                    for o in touching
                )
                if not covered:
                    hole = Hole(_at(k, plane, (w, row), (Z, z)), k, -f.side, d, depth)
                    holes.append((p, hole))
    return holes


def _plan_fitting(
    f: Fitting, instances: Mapping[str, list[_Instance]], boards: Mapping[str, BoardState]
) -> tuple[list[_FittingHole], dict[_HardwareKey, int]]:
    """Holes of one fitting and its hardware (raises _PlanError)."""
    ids = f.board_ids()
    if any(i not in boards for i in ids):
        raise _PlanError(JointErrorCode.NO_CONTACT)
    if any(boards[i].rotated for i in ids):
        raise _PlanError(JointErrorCode.ROTATED)
    _check_fitting_params(f)
    part = boards[f.part_id].orientation.thickness_axis.position
    if f.kind == FittingKind.SHELF_PINS:
        others = [o for items in instances.values() for o in items]
        return _plan_shelf_pins(f, instances[f.part_id], part, others), {}
    assert f.carrier_id is not None
    carrier = boards[f.carrier_id].orientation.thickness_axis.position
    if f.kind == FittingKind.HINGE:
        if part != Y or carrier != X:
            raise _PlanError(JointErrorCode.ORIENTATION)
        holes, cups = _plan_hinge(f, instances[f.part_id], instances[f.carrier_id])
        return holes, {(FittingKind.HINGE, f.param("cup_diameter"), 0.0): cups}
    if part != X or carrier != X:
        raise _PlanError(JointErrorCode.ORIENTATION)
    holes, lengths = _plan_slide(f, instances[f.part_id], instances[f.carrier_id])
    return holes, {(FittingKind.SLIDE, 0.0, length): n for length, n in lengths.items()}


def fitting_holes(boards: Mapping[str, BoardState], fitting: Fitting) -> list[tuple[str, Hole]]:
    """World holes ``fitting`` would drill (for previews; empty if it cannot be built)."""
    instances = {
        board_id: [
            _Instance(list(origin), [origin[i] + state.size[i] for i in range(3)])
            for origin in state.instance_origins()
        ]
        for board_id, state in boards.items()
    }
    owner = {id(i): board_id for board_id, items in instances.items() for i in items}
    try:
        holes, _hardware = _plan_fitting(fitting, instances, boards)
    except _PlanError:
        return []
    return [(owner[id(instance)], hole) for instance, hole in holes]


def _sketch_feature(state: BoardState, origin: Vec3, sketch: SketchFeature) -> Feature:
    o = state.orientation
    axes = (o.length_axis.position, o.width_axis.position, o.thickness_axis.position)
    start = list(origin)
    if not sketch.cut:
        return Extrusion(sketch.regions, axes, origin, state.thickness, cut=False)
    if sketch.depth is None or sketch.depth >= state.thickness:
        length = state.thickness
    else:
        length = sketch.depth
        if sketch.top:
            start[axes[2]] += state.thickness - length
    return Extrusion(sketch.regions, axes, _vec(start), length, cut=True)


def compute_machining(
    doc: Document,
    boards: Mapping[str, BoardState],
    sketches: Mapping[str, list[SketchFeature]] | None = None,
) -> Machining:
    instances = {
        board_id: [
            _Instance(list(origin), [origin[i] + state.size[i] for i in range(3)])
            for origin in state.instance_origins()
        ]
        for board_id, state in boards.items()
    }
    for board_id, features in (sketches or {}).items():
        if board_id not in boards:
            continue
        for instance in instances[board_id]:
            for sketch in features:
                instance.features.append(
                    _sketch_feature(boards[board_id], _vec(instance.lo), sketch)
                )
                instance.cut_operations += sketch.cut
    result = Machining()
    joint_hardware: dict[_HardwareKey, int] = {}
    spans: list[tuple[_Instance, _GrooveSpan]] = []
    for joint in doc.joints.values():
        if joint.main_id not in boards or joint.attached_id not in boards:
            continue
        if boards[joint.main_id].rotated or boards[joint.attached_id].rotated:
            result.errors[joint.id] = JointErrorCode.ROTATED
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
            if isinstance(op[1], _GrooveSpan):
                spans.append((op[0], op[1]))
        if count:
            key = (joint.kind, joint.param("diameter"), joint.param("length"))
            joint_hardware[key] = joint_hardware.get(key, 0) + count
    for main, span in spans:
        _widen(main, span)
    hardware: dict[_HardwareKey, int] = dict(joint_hardware)
    for fitting in doc.fittings.values():
        try:
            holes, used = _plan_fitting(fitting, instances, boards)
        except _PlanError as failure:
            result.errors[fitting.id] = failure.code
            continue
        for instance, hole in holes:
            instance.features.append(hole)
        for item, n in used.items():
            hardware[item] = hardware.get(item, 0) + n
    result.parts = {
        board_id: [_rotated(i.shape(), boards[board_id]) for i in items]
        for board_id, items in instances.items()
    }
    result.hardware = [
        Hardware(kind, diameter, length, quantity)
        for (kind, diameter, length), quantity in sorted(hardware.items())
    ]
    return result


def _rotated(shape: PartShape, state: BoardState) -> PartShape:
    if not state.rotated:
        return shape
    rotation = (state.rotation_axis.position, state.angle % 360.0)
    return PartShape(shape.origin, shape.size, shape.features, shape.cut_operations, rotation)


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
        elif isinstance(f, Extrusion):
            # Sketch coordinates are already in the board's own frame.
            items.append(("extrusion", f.regions, local(f.origin), round(f.length, _ROUND), f.cut))
        else:
            items.append(("cutout", local(f.lo), local(f.hi)))
    return local(shape.size), tuple(sorted(items, key=repr))
