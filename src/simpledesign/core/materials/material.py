"""Material = raw stock a part is cut from."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaterialKind(StrEnum):
    PANEL = "panel"  # sheet goods: plywood, MDF, chipboard, glued panels
    SOLID = "solid"  # solid wood boards/planks
    TIMBER = "timber"  # construction timber / battens with a fixed cross-section


@dataclass(frozen=True, slots=True)
class Material:
    id: str
    name: str
    kind: MaterialKind
    thickness: float  # mm
    # Fixed second cross-section dimension (construction timber); None = free.
    width: float | None = None
    # True: the part's length must follow the grain (cut list keeps L/W apart).
    has_grain: bool = True
    color: str = "#d9b98c"
