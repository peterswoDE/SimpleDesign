"""Document-wide settings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CutSettings:
    """Cutting plan settings (Zuschnittoptimierung)."""

    kerf: float = 4.0  # saw blade width (Sägeblattbreite), mm
    # Extra length per part cut from bars, for squaring the ends (Kappzugabe), mm.
    crosscut_allowance: float = 10.0
