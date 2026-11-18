"""Camera math that is independent of VTK (and therefore easy to test)."""

from __future__ import annotations

import math
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


def _rotate(v: Vec3, axis: Vec3, degrees: float) -> Vec3:
    """Rodrigues rotation of ``v`` about the unit vector ``axis``."""
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    ax, ay, az = axis
    dot = ax * v[0] + ay * v[1] + az * v[2]
    cross = (ay * v[2] - az * v[1], az * v[0] - ax * v[2], ax * v[1] - ay * v[0])
    return (
        v[0] * c + cross[0] * s + ax * dot * (1 - c),
        v[1] * c + cross[1] * s + ay * dot * (1 - c),
        v[2] * c + cross[2] * s + az * dot * (1 - c),
    )


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _normalized(v: Vec3) -> Vec3:
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / length, v[1] / length, v[2] / length) if length > 1e-12 else (0.0, 0.0, 0.0)


MAX_ELEVATION = 89.0


def turntable(
    position: Vec3, focal: Vec3, up: Vec3, pivot: Vec3, azimuth: float, elevation: float
) -> tuple[Vec3, Vec3, Vec3]:
    """Orbit the camera about ``pivot``: ``azimuth`` about world Z, ``elevation``
    about the horizontal screen axis. Z stays up; the camera never flips over a pole.

    Returns the new position, focal point and view-up vector.
    """
    z = (0.0, 0.0, 1.0)
    # 1) azimuth about the vertical axis through the pivot
    position = _add(pivot, _rotate(_sub(position, pivot), z, azimuth))
    focal = _add(pivot, _rotate(_sub(focal, pivot), z, azimuth))
    up = _rotate(up, z, azimuth)

    # 2) elevation, clamped so the view direction never reaches ±Z
    back = _normalized(_sub(position, focal))  # from focal point towards the camera
    current = math.degrees(math.asin(max(-1.0, min(1.0, back[2]))))
    target = max(-MAX_ELEVATION, min(MAX_ELEVATION, current + elevation))
    if abs(current) > MAX_ELEVATION:  # e.g. the top view: only allow moving away from the pole
        target = current + elevation if abs(current + elevation) < abs(current) else current
    delta = target - current
    right = _normalized((-back[1], back[0], 0.0))  # horizontal, perpendicular to the view
    if right == (0.0, 0.0, 0.0):  # looking straight down/up: use the screen's right axis
        right = _normalized(
            (
                up[1] * back[2] - up[2] * back[1],
                up[2] * back[0] - up[0] * back[2],
                up[0] * back[1] - up[1] * back[0],
            )
        )
    if delta and right != (0.0, 0.0, 0.0):
        # positive elevation lifts the camera: rotate about -right
        position = _add(pivot, _rotate(_sub(position, pivot), right, -delta))
        focal = _add(pivot, _rotate(_sub(focal, pivot), right, -delta))
        up = _rotate(up, right, -delta)

    back = _normalized(_sub(position, focal))
    if abs(back[2]) < 0.9999:
        # Keep the world Z axis pointing up on screen.
        up = _normalized(_sub(z, (back[0] * back[2], back[1] * back[2], back[2] * back[2])))
    return position, focal, up
