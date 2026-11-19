"""Edge banding (Kantenumleimer): a strip glued onto a narrow edge of a part.

The part's length and width formulas are *finished* sizes, banding included.
The panel is cut smaller by the banding thickness on every banded edge
(rough size = finished size minus banding).

Edges in the part's local frame (length = local X, width = local Y):
``L1``/``L2`` are the long edges along the length at width 0 / width max,
``W1``/``W2`` the ends at length 0 / length max.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BandEdge(StrEnum):
    L1 = "l1"  # along the length, at width 0
    L2 = "l2"  # along the length, at width max
    W1 = "w1"  # across, at length 0
    W2 = "w2"  # across, at length max

    @property
    def along_length(self) -> bool:
        """True for the long edges: they run along the length (and shorten the width)."""
        return self in (BandEdge.L1, BandEdge.L2)

    @property
    def at_end(self) -> bool:
        """True at width max / length max."""
        return self in (BandEdge.L2, BandEdge.W2)

    def swapped(self) -> BandEdge:
        """The same edge after turning the part by 90° (length <-> width)."""
        return _SWAPPED[self]


_SWAPPED = {
    BandEdge.L1: BandEdge.W1,
    BandEdge.L2: BandEdge.W2,
    BandEdge.W1: BandEdge.L1,
    BandEdge.W2: BandEdge.L2,
}

# Extra length per banded edge: the strip overhangs at both ends and is
# trimmed flush after gluing.
EDGE_EXCESS = 30.0  # mm


@dataclass(frozen=True, slots=True)
class EdgeBand:
    id: str
    name: str
    thickness: float  # mm, taken off the panel
    color: str = "#f0f0f0"


BUILTIN_EDGE_BANDS: tuple[EdgeBand, ...] = (
    EdgeBand("builtin.abs_04_white", "ABS 0,4 mm weiß", 0.4, "#f4f4f2"),
    EdgeBand("builtin.abs_1_white", "ABS 1 mm weiß", 1.0, "#f4f4f2"),
    EdgeBand("builtin.abs_2_white", "ABS 2 mm weiß", 2.0, "#f4f4f2"),
    EdgeBand("builtin.abs_2_anthracite", "ABS 2 mm anthrazit", 2.0, "#3c3f44"),
    EdgeBand("builtin.veneer_oak", "Furnier Eiche 0,5 mm", 0.5, "#c8a06a"),
    EdgeBand("builtin.veneer_beech", "Furnier Buche 0,5 mm", 0.5, "#dcb48c"),
    EdgeBand("builtin.solid_oak_5", "Anleimer Eiche 5 mm", 5.0, "#b98d55"),
)
_BUILTIN = {band.id: band for band in BUILTIN_EDGE_BANDS}


def builtin_edge_band(band_id: str) -> EdgeBand | None:
    return _BUILTIN.get(band_id)


def edge_band_to_dict(band: EdgeBand) -> dict[str, Any]:
    return {"id": band.id, "name": band.name, "thickness": band.thickness, "color": band.color}


def edge_band_from_dict(data: dict[str, Any]) -> EdgeBand:
    """Raises KeyError/TypeError/ValueError on invalid data."""
    return EdgeBand(
        id=str(data["id"]),
        name=str(data["name"]),
        thickness=float(data["thickness"]),
        color=str(data.get("color", "#f0f0f0")),
    )
