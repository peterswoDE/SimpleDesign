"""Camera math that is independent of VTK (and therefore easy to test)."""

from __future__ import annotations

from typing import NamedTuple

Vec3 = tuple[float, float, float]


class StandardView(NamedTuple):
    """Direction from the focal point towards the camera, and the camera's up vector."""

    direction: Vec3
    up: Vec3


# Z points up, X right, Y backwards (plan section 3).
STANDARD_VIEWS: dict[str, StandardView] = {
    "front": StandardView((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": StandardView((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": StandardView((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": StandardView((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top": StandardView((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "iso": StandardView((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
}


def scale_about(point: Vec3, center: Vec3, factor: float) -> Vec3:
    """Scale ``point`` towards ``center``: ``center + (point - center) / factor``.

    Used for "zoom to cursor": moving camera position *and* focal point this way
    is a uniform scaling of the view about ``center``, so ``center`` stays exactly
    under the mouse cursor while the view direction is unchanged.
    """
    if factor <= 0:
        raise ValueError("factor must be positive")
    return (
        center[0] + (point[0] - center[0]) / factor,
        center[1] + (point[1] - center[1]) / factor,
        center[2] + (point[2] - center[2]) / factor,
    )
