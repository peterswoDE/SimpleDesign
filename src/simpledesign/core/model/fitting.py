"""Fittings (Beschläge) with their drilling pattern (Bohrbild).

A fitting belongs to a *part* and, except for shelf pin rows, is held by a
*carrier*:

* hinge (Topfband): part = door, carrier = the side it swings on. Cup holes
  in the door, mounting plate holes in the carrier.
* drawer slide (Schubkastenschiene): part = drawer side (Zarge), carrier =
  the carcass side next to it. Holes in both.
* shelf pin row (Lochreihe): holes on one face of a part (``side`` +1/-1
  along its thickness axis) for adjustable shelves.
* handle (Griff / Knopf): part = door or drawer front, no carrier. Through
  holes for the screws; on a door opposite its hinges, on a drawer front
  in the middle. ``hole_spacing`` 0 is a knob.

As with joints, positions are not stored: they come from the computed
geometry every time, so the holes follow every change. Settings are plain
numbers (mm); 0 for ``count`` / ``length`` means automatic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FittingKind(StrEnum):
    HINGE = "hinge"  # Topfband
    SLIDE = "slide"  # Schubkastenschiene
    SHELF_PINS = "shelf_pins"  # Lochreihe für Bodenträger
    HANDLE = "handle"  # Griff oder Knopf


# fittings on one part only (no carrier)
CARRIERLESS = frozenset({FittingKind.SHELF_PINS, FittingKind.HANDLE})


FITTING_DEFAULTS: dict[FittingKind, dict[str, float]] = {
    FittingKind.HINGE: {
        "cup_diameter": 35.0,
        "cup_depth": 13.0,
        "cup_distance": 21.5,  # cup centre from the door edge
        "end_distance": 100.0,  # first/last hinge from the door ends
        "count": 0.0,  # 0 = by door height
        "plate_distance": 37.0,  # plate holes behind the front edge of the carrier
        "plate_hole_diameter": 5.0,
        "plate_hole_depth": 12.0,
    },
    FittingKind.SLIDE: {
        "length": 0.0,  # nominal length; 0 = longest that fits the drawer side
        "front_distance": 37.0,  # first hole behind the front of the drawer side
        "hole_diameter": 5.0,  # in the carcass side
        "hole_depth": 12.0,
        "part_hole_diameter": 2.5,  # pilot holes in the drawer side
        "part_hole_depth": 8.0,
    },
    FittingKind.SHELF_PINS: {
        "diameter": 5.0,
        "depth": 12.0,
        "front_distance": 37.0,
        "back_distance": 37.0,
        "spacing": 32.0,  # System 32
        "first": 96.0,  # lowest hole above the bottom of the part
        "last": 96.0,  # highest hole below the top of the part
    },
    FittingKind.HANDLE: {
        "hole_spacing": 128.0,  # Bohrabstand of a bar handle; 0 = knob
        "edge_distance": 40.0,  # door: handle centre from the edge opposite the hinges
        "top_distance": 0.0,  # handle centre below the top edge; 0 = middle
        "screw_diameter": 5.0,  # through holes
        "grip_diameter": 12.0,  # thickness of the bar / knob (for the 3D view)
        "projection": 30.0,  # how far the handle stands out of the front
    },
}

SYSTEM_32 = 32.0
SLIDE_LENGTHS = tuple(range(250, 651, 50))


def default_params(kind: FittingKind) -> dict[str, float]:
    return dict(FITTING_DEFAULTS[kind])


def hinge_count(height: float) -> int:
    """Hinges for a door of ``height`` (manufacturer rule of thumb)."""
    for limit, count in ((900, 2), (1600, 3), (2200, 4)):
        if height <= limit:
            return count
    return 5


def slide_length(depth: float) -> int | None:
    """Longest standard slide for a drawer side of ``depth``."""
    fitting = [n for n in SLIDE_LENGTHS if n <= depth + 1e-9]
    return fitting[-1] if fitting else None


@dataclass(slots=True)
class Fitting:
    id: str
    kind: FittingKind
    part_id: str
    carrier_id: str | None = None
    side: int = 1  # shelf pins: face along the part's thickness axis
    params: dict[str, float] = field(default_factory=dict)

    def param(self, name: str) -> float:
        return self.params.get(name, FITTING_DEFAULTS[self.kind][name])

    def board_ids(self) -> list[str]:
        return [self.part_id] if self.carrier_id is None else [self.part_id, self.carrier_id]
