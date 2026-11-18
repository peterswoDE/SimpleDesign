"""Material = raw stock a part is cut from."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


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
    # Size of one piece of stock as bought. Panels/solid wood: a sheet or plank
    # (length = along the grain). Timber: a bar (only the length counts).
    # None = unknown -> no cutting plan, only the demand is listed.
    stock_length: float | None = None
    stock_width: float | None = None
    # Waste cut off each edge of a sheet before cutting (Besäumrand), mm.
    edge_trim: float = 0.0
    # Price of one piece of stock (sheet, plank, bar); None = unknown.
    price: float | None = None

    @property
    def is_linear(self) -> bool:
        """Cut from bars by length only (1D) instead of from sheets (2D)."""
        return self.kind == MaterialKind.TIMBER


def material_to_dict(material: Material) -> dict[str, Any]:
    data = asdict(material)
    data["kind"] = material.kind.value
    return data


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def material_from_dict(data: dict[str, Any]) -> Material:
    """Raises KeyError/TypeError/ValueError on invalid data."""
    return Material(
        id=str(data["id"]),
        name=str(data["name"]),
        kind=MaterialKind(data["kind"]),
        thickness=float(data["thickness"]),
        width=_optional_float(data["width"]),
        has_grain=bool(data["has_grain"]),
        color=str(data["color"]),
        stock_length=_optional_float(data["stock_length"]),
        stock_width=_optional_float(data["stock_width"]),
        edge_trim=float(data["edge_trim"]),
        price=_optional_float(data["price"]),
    )
