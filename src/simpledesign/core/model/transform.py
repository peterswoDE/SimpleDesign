"""Resize a board at one face and rotate it about its centre.

Both keep existing formulas: the change is added as an offset
(``Seite.length - 36`` → ``Seite.length - 36 + 50``), so the part keeps
following the rest of the furniture.
"""

from __future__ import annotations

import numpy as np

from simpledesign.core.geometry.placement import rotation_matrix
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.part import Axis
from simpledesign.core.params.expression import add_offset

MIN_SIZE = 1.0  # mm


def resizable(doc: Document, evaluation: Evaluation, board_id: str, axis: Axis) -> bool:
    """Whether the size along world ``axis`` can be dragged."""
    board = doc.boards[board_id]
    state = evaluation.boards.get(board_id)
    if state is None or state.rotated:
        return False
    dim = state.orientation.dimension_along(axis)
    return board.profile is None or dim == "thickness"


def resize(
    doc: Document, evaluation: Evaluation, board_id: str, axis: Axis, side: int, delta: float
) -> None:
    """Move the face on ``side`` (+1: end face, -1: start face) by ``delta``
    along ``axis``; the opposite face stays where it is."""
    if not resizable(doc, evaluation, board_id, axis):
        raise ValueError("this size cannot be changed here")
    state = evaluation.boards[board_id]
    if state.extent(axis) + side * delta < MIN_SIZE:
        raise ValueError("size would be zero or negative")
    dim = state.orientation.dimension_along(axis)
    current = doc.display_expression(board_id, dim)
    if side > 0:
        doc.set_expression(board_id, dim, add_offset(current, delta))
    else:
        position = doc.display_expression(board_id, axis.value)
        doc.set_expression(board_id, axis.value, add_offset(position, delta))
        doc.set_expression(board_id, dim, add_offset(current, -delta))


def rotation_shift(
    size: tuple[float, float, float], axis: int, old_angle: float, new_angle: float
) -> tuple[float, float, float]:
    """How far the position corner moves so that the centre stays in place
    when the rotation about ``axis`` (through the corner) changes."""
    half = np.asarray(size) / 2
    old = rotation_matrix((axis, old_angle)) @ half
    new = rotation_matrix((axis, new_angle)) @ half
    shift = old - new
    return float(shift[0]), float(shift[1]), float(shift[2])


def rotate(
    doc: Document, evaluation: Evaluation, board_id: str, axis: Axis, degrees: float
) -> None:
    """Turn a board by ``degrees`` about world ``axis`` through its centre.

    A board has one rotation axis: an already rotated board can only be turned
    further about that axis.
    """
    board = doc.boards[board_id]
    state = evaluation.boards[board_id]
    if state.rotated and board.rotation_axis != axis:
        raise ValueError("already rotated about another axis")
    old = state.angle if state.rotated else 0.0
    shift = rotation_shift(state.size, axis.position, old, old + degrees)
    board.rotation_axis = axis
    angle = doc.display_expression(board_id, "angle") if state.rotated else "0"
    doc.set_expression(board_id, "angle", add_offset(angle, degrees))
    for prop, amount in zip(("x", "y", "z"), shift, strict=True):
        if abs(amount) > 1e-6:
            current = doc.display_expression(board_id, prop)
            doc.set_expression(board_id, prop, add_offset(current, round(amount, 3)))
