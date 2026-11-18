"""Computed state of a board (result of evaluating its formulas)."""

from __future__ import annotations

from dataclasses import dataclass

from simpledesign.core.model.part import Axis, Orientation

type Vec3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class BoardState:
    """Computed values of one board (the *visible* size, without joint allowances)."""

    length: float
    width: float
    thickness: float
    x: float
    y: float
    z: float
    count: int
    spacing: float
    orientation: Orientation
    pattern_axis: Axis
    angle: float = 0.0  # degrees about rotation_axis
    rotation_axis: Axis = Axis.Z

    @property
    def rotated(self) -> bool:
        return abs(self.angle % 360.0) > 1e-9

    def extent(self, axis: Axis) -> float:
        return float(getattr(self, self.orientation.dimension_along(axis)))

    @property
    def size(self) -> Vec3:
        """World-axis-aligned size (sx, sy, sz)."""
        return (self.extent(Axis.X), self.extent(Axis.Y), self.extent(Axis.Z))

    @property
    def origin(self) -> Vec3:
        return (self.x, self.y, self.z)

    def instance_origins(self) -> list[Vec3]:
        """Min corner of every copy of the pattern (first = the board itself)."""
        origins = []
        for i in range(self.count):
            point = [self.x, self.y, self.z]
            point[self.pattern_axis.position] += i * self.spacing
            origins.append((point[0], point[1], point[2]))
        return origins
