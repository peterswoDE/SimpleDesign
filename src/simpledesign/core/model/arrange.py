"""Arrange parts: align and distribute boards and groups along an axis.

Every object (board or group with its contents) is moved as a whole with
``Document.translate``. Objects are moved one after the other and the
document is re-evaluated in between, so a part whose position formula
follows another moved part is not moved twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from simpledesign.core.geometry.placement import world_bounds
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.part import Axis

Vec3 = tuple[float, float, float]
Box = tuple[Vec3, Vec3]

AXIS_INDEX = {Axis.X: 0, Axis.Y: 1, Axis.Z: 2}


class AlignMode(StrEnum):
    MIN = "min"  # left / front / bottom
    CENTER = "center"
    MAX = "max"  # right / back / top


def bounds(doc: Document, ids: Sequence[str], evaluation: Evaluation | None = None) -> Box | None:
    """World bounding box of the boards in ``ids`` (groups with their contents)."""
    if evaluation is None:
        evaluation = Evaluator().evaluate(doc)
    members = doc.with_contents(ids)
    lows: list[Vec3] = []
    highs: list[Vec3] = []
    for board_id, shapes in evaluation.machining.parts.items():
        if board_id not in members:
            continue
        for shape in shapes:
            lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
            lows.append(lo)
            highs.append(hi)
    if not lows:
        return None
    low = (min(p[0] for p in lows), min(p[1] for p in lows), min(p[2] for p in lows))
    high = (max(p[0] for p in highs), max(p[1] for p in highs), max(p[2] for p in highs))
    return low, high


def _shift(doc: Document, obj_id: str, axis: Axis, amount: float) -> None:
    if abs(amount) < 1e-9:
        return
    delta = [0.0, 0.0, 0.0]
    delta[AXIS_INDEX[axis]] = amount
    doc.translate([obj_id], (delta[0], delta[1], delta[2]))


def _key(box: Box, axis: Axis, mode: AlignMode) -> float:
    i = AXIS_INDEX[axis]
    if mode == AlignMode.MIN:
        return box[0][i]
    if mode == AlignMode.MAX:
        return box[1][i]
    return (box[0][i] + box[1][i]) / 2


def align(doc: Document, ids: Sequence[str], axis: Axis, mode: AlignMode) -> None:
    """Align all objects to the first one (the reference stays where it is)."""
    if len(ids) < 2:
        return
    reference = bounds(doc, ids[:1])
    if reference is None:
        return
    target = _key(reference, axis, mode)
    for obj_id in ids[1:]:
        box = bounds(doc, [obj_id])
        if box is not None:
            _shift(doc, obj_id, axis, target - _key(box, axis, mode))


def distribute(doc: Document, ids: Sequence[str], axis: Axis) -> None:
    """Equal gaps between the objects; the outermost two stay in place."""
    if len(ids) < 3:
        return
    i = AXIS_INDEX[axis]
    boxes = {obj_id: bounds(doc, [obj_id]) for obj_id in ids}
    placed = sorted(
        ((obj_id, box) for obj_id, box in boxes.items() if box is not None),
        key=lambda item: item[1][0][i],
    )
    if len(placed) < 3:
        return
    start = placed[0][1][1][i]
    end = placed[-1][1][0][i]
    inner = placed[1:-1]
    sizes = sum(box[1][i] - box[0][i] for _id, box in inner)
    gap = (end - start - sizes) / (len(inner) + 1)
    position = start + gap
    for obj_id, _box in inner:
        current = bounds(doc, [obj_id])  # may have followed an earlier move
        if current is None:
            continue
        _shift(doc, obj_id, axis, position - current[0][i])
        position += current[1][i] - current[0][i] + gap


def gaps(
    doc: Document, first: str, second: str, evaluation: Evaluation | None = None
) -> Vec3 | None:
    """Clear distance between two objects per axis (negative = overlap)."""
    a = bounds(doc, [first], evaluation)
    b = bounds(doc, [second], evaluation)
    if a is None or b is None:
        return None
    result = [max(b[0][k] - a[1][k], a[0][k] - b[1][k]) for k in range(3)]
    return (result[0], result[1], result[2])
