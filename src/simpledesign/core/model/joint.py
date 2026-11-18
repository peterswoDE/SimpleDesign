"""Wood joints (Holzverbindungen).

A joint connects a *main part* (Hauptteil) and an *attached part*
(Anschlussteil) that touch each other. It may

* remove material (holes, grooves, rabbets) from either part and
* extend the attached part into the main part (insertion into a groove).
  The extension is part of the rough size in the cut list.

Where exactly the parts touch is not stored: it is found from the computed
geometry every time, so a joint follows all parameter changes. Joint settings
are plain numbers (mm).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class JointKind(StrEnum):
    DOWEL = "dowel"  # Dübel
    SCREW = "screw"  # Schraube (through the main part into the attached part)
    GROOVE = "groove"  # Nut
    RABBET = "rabbet"  # Falz


# Settings per kind with their defaults. ``count`` 0 = automatic from the rules
# (edge distance + maximum spacing).
JOINT_DEFAULTS: dict[JointKind, dict[str, float]] = {
    JointKind.DOWEL: {
        "diameter": 8.0,
        "length": 40.0,
        "depth_main": 12.0,  # hole depth in the main part
        "edge_distance": 50.0,
        "max_spacing": 150.0,
        "count": 0.0,
    },
    JointKind.SCREW: {
        "diameter": 4.0,
        "length": 50.0,
        "edge_distance": 50.0,
        "max_spacing": 200.0,
        "count": 0.0,
    },
    JointKind.GROOVE: {
        "depth": 10.0,  # groove depth in the main part
        "play": 0.5,  # groove wider than the part; part shorter than the groove depth
    },
    JointKind.RABBET: {
        "depth": 10.0,
        "play": 0.5,
    },
}


def default_params(kind: JointKind) -> dict[str, float]:
    return dict(JOINT_DEFAULTS[kind])


@dataclass(slots=True)
class Joint:
    id: str
    kind: JointKind
    main_id: str
    attached_id: str
    params: dict[str, float] = field(default_factory=dict)

    def param(self, name: str) -> float:
        return self.params.get(name, JOINT_DEFAULTS[self.kind][name])
