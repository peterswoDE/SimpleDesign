"""Gizmo maths without VTK: drag along an axis and snap to other boards.

A snap does not just set a number - it writes a *formula* (``Seite.x2``), so
the board stays attached when the other board moves later.
"""

from __future__ import annotations

from dataclasses import dataclass

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.part import Axis

type Vec3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class SnapTarget:
    value: float  # new start coordinate of the dragged board along the axis
    formula: str


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def axis_parameter(origin: Vec3, direction: Vec3, ray_start: Vec3, ray_direction: Vec3) -> float:
    """Parameter ``t`` of the point on the axis ``origin + t*direction`` closest to a ray.

    Both directions must be unit vectors. Returns 0 when they are parallel
    (the user looks straight along the axis - no sensible drag possible).
    """
    a = _dot(direction, ray_direction)
    denominator = 1.0 - a * a
    if denominator < 1e-9:
        return 0.0
    r = _sub(origin, ray_start)
    return (a * _dot(ray_direction, r) - _dot(direction, r)) / denominator


def snap_targets(
    doc: Document, evaluation: Evaluation, board_id: str, axis: Axis
) -> list[SnapTarget]:
    """All positions where a face of the dragged board meets a face of another board (or 0)."""
    me = doc.boards[board_id]
    dim = evaluation.boards[board_id].orientation.dimension_along(axis)
    extent = evaluation.boards[board_id].extent(axis)
    own_size = f"{me.name}.{dim}"
    start_prop, end_prop = axis.value, f"{axis.value}2"

    targets = [SnapTarget(0.0, "0"), SnapTarget(-extent, f"-{own_size}")]
    for other in doc.boards.values():
        if other.id == board_id or not doc.is_visible(other):
            continue
        state = evaluation.boards[other.id]
        repeats = state.count if state.pattern_axis == axis else 1
        for i in range(repeats):
            shift = i * state.spacing
            suffix = f" + {i} * {other.name}.spacing" if i else ""
            start = state.origin[axis.position] + shift
            end = start + state.extent(axis)
            for face, prop in ((start, start_prop), (end, end_prop)):
                face_formula = f"{other.name}.{prop}{suffix}"
                targets.append(SnapTarget(face, face_formula))  # my start on the face
                targets.append(SnapTarget(face - extent, f"{face_formula} - {own_size}"))
    return targets


def best_snap(targets: list[SnapTarget], raw: float, tolerance: float) -> SnapTarget | None:
    """The target closest to ``raw`` within ``tolerance`` (first one wins on ties)."""
    best: SnapTarget | None = None
    for target in targets:
        distance = abs(target.value - raw)
        if distance <= tolerance and (best is None or distance < abs(best.value - raw)):
            best = target
    return best
