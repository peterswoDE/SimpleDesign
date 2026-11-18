"""Model objects: parameters, groups and boards.

Coordinates: Z up, X right, Y backwards, millimetres.
A board's local X axis is its *length* and the grain direction, local Y its
width, local Z its thickness. In phase 1 boards are axis-aligned; the
:class:`Orientation` says along which world axes length and width run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from simpledesign.core.sketch.model import Sketch


class Axis(StrEnum):
    X = "x"
    Y = "y"
    Z = "z"

    @property
    def position(self) -> int:
        """0, 1, 2 for x, y, z (index into a coordinate tuple)."""
        return "xyz".index(self.value)


class Orientation(StrEnum):
    """``<length axis><width axis>``; the thickness runs along the remaining axis."""

    XY = "xy"  # lying, length along X  (bottom, top, shelf)
    YX = "yx"  # lying, length along Y
    XZ = "xz"  # upright, length along X, thickness along Y (back panel, front)
    ZX = "zx"  # upright, length along Z, thickness along Y (back panel, door)
    ZY = "zy"  # upright, length along Z, thickness along X (side panel)
    YZ = "yz"  # upright, length along Y, thickness along X

    @property
    def length_axis(self) -> Axis:
        return Axis(self.value[0])

    @property
    def width_axis(self) -> Axis:
        return Axis(self.value[1])

    @property
    def thickness_axis(self) -> Axis:
        (rest,) = set("xyz") - set(self.value)
        return Axis(rest)

    def dimension_along(self, axis: Axis) -> str:
        """Which of ``length``/``width``/``thickness`` runs along world ``axis``."""
        if axis == self.length_axis:
            return "length"
        if axis == self.width_axis:
            return "width"
        return "thickness"


DIMENSIONS = ("length", "width", "thickness")
POSITION = ("x", "y", "z")
PATTERN = ("count", "spacing")
EDITABLE_PROPERTIES = DIMENSIONS + POSITION + PATTERN
# Read-only, computed: end coordinate along each axis (x2 = x + size along X).
DERIVED_PROPERTIES = ("x2", "y2", "z2")
PROPERTIES = EDITABLE_PROPERTIES + DERIVED_PROPERTIES

# Formula keys of sketches: ``d.<constraint id>`` (dimension), ``depth.<cut id>``.
SKETCH_PREFIXES = ("d.", "depth.")


def is_sketch_property(prop: str) -> bool:
    return prop.startswith(SKETCH_PREFIXES)


END_PROPERTY = {Axis.X: "x2", Axis.Y: "y2", Axis.Z: "z2"}


def default_expressions() -> dict[str, str]:
    return {
        "length": "600",
        "width": "300",
        "thickness": "18",
        "x": "0",
        "y": "0",
        "z": "0",
        "count": "1",
        "spacing": "0",
    }


@dataclass(slots=True)
class Parameter:
    id: str
    name: str
    expression: str  # stored form (references as IDs)
    group_id: str | None = None  # None = global


@dataclass(slots=True)
class Group:
    id: str
    name: str
    parent_id: str | None = None
    visible: bool = True


class Face(StrEnum):
    """Face of a board a cut-out sketch sits on (seen from the top side)."""

    TOP = "top"  # + thickness side
    BOTTOM = "bottom"


@dataclass(slots=True)
class SketchCut:
    """A pocket or through cut-out drawn as a sketch on a board face.

    Sketch coordinates: u along the length, v along the width, from the
    board's corner - as seen from the top, also for the bottom face.
    Depth formula: ``board.expressions["depth.<id>"]``.
    """

    id: str
    sketch: Sketch
    face: Face = Face.TOP
    through: bool = False

    @property
    def depth_key(self) -> str:
        return f"depth.{self.id}"


@dataclass(slots=True)
class Board:
    id: str
    name: str
    material_id: str
    group_id: str | None = None
    orientation: Orientation = Orientation.XY
    # Stored formula per EDITABLE_PROPERTIES entry.
    expressions: dict[str, str] = field(default_factory=default_expressions)
    # Pattern ("Anordnung"): `count` copies, `spacing` apart along this axis.
    pattern_axis: Axis = Axis.Z
    visible: bool = True
    transparent: bool = False
    # Grain direction does not matter for this part (e.g. hidden inner parts):
    # the cut list may turn it. Has no effect for materials without grain.
    ignore_grain: bool = False
    # Sketched outline: the part is this profile extruded by the thickness.
    # Length and width then follow from the profile (bounding box).
    profile: Sketch | None = None
    cuts: list[SketchCut] = field(default_factory=list)

    def sketches(self) -> list[tuple[str, Sketch]]:
        """(key, sketch) of the profile and every cut; keys as in Evaluation.sketches."""
        found = [(self.id, self.profile)] if self.profile is not None else []
        found += [(f"{self.id}/{cut.id}", cut.sketch) for cut in self.cuts]
        return found

    def formula_properties(self) -> list[str]:
        """Every property with a formula (profile boards have no length/width formula)."""
        props = [
            p for p in EDITABLE_PROPERTIES if self.profile is None or p not in ("length", "width")
        ]
        return props + [p for p in self.expressions if is_sketch_property(p)]
