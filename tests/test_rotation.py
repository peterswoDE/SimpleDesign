"""Phase 7: free rotation of boards."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from simpledesign.core.geometry.placement import rotation_matrix, world_bounds
from simpledesign.core.io import sdz
from simpledesign.core.io.export import export
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.machining import JointErrorCode
from simpledesign.core.model.part import Axis
from tests.furniture import board_id, build_korpus


def rotated_board() -> tuple[Document, str]:
    doc = Document()
    board = doc.add_board("Brett")
    for prop, text in {"length": "100", "width": "50", "thickness": "10", "angle": "90"}.items():
        doc.set_expression(board.id, prop, text)
    return doc, board.id


def test_rotation_matrix_turns_x_into_y() -> None:
    assert rotation_matrix((2, 90.0)) @ np.array([1.0, 0, 0]) == pytest.approx([0, 1, 0])
    lo, hi = world_bounds((0.0, 0.0, 0.0), (100.0, 50.0, 10.0), (2, 90.0))
    assert lo == pytest.approx((-50, 0, 0)) and hi == pytest.approx((0, 100, 10))


def test_rotated_part_shape_and_state() -> None:
    doc, bid = rotated_board()
    ev = Evaluator().evaluate(doc)
    assert ev.boards[bid].rotated and ev.boards[bid].angle == 90
    (part,) = ev.machining.parts[bid]
    assert part.rotation == (2, 90.0)
    assert part.size == pytest.approx((100, 50, 10))  # local size unchanged (cut list)


def test_joint_with_rotated_board_reports_error() -> None:
    doc = build_korpus()
    side, bottom = board_id(doc, "Seite_links"), board_id(doc, "Boden")
    joint = doc.add_joint(JointKind.DOWEL, side, bottom)
    doc.set_expression(bottom, "angle", "5")
    ev = Evaluator().evaluate(doc)
    assert ev.machining.errors[joint.id] == JointErrorCode.ROTATED


def test_stl_export_is_rotated(tmp_path: Path) -> None:
    doc, _ = rotated_board()
    path = tmp_path / "r.stl"
    export(doc, Evaluator().evaluate(doc), path)
    data = path.read_bytes()
    (count,) = struct.unpack_from("<I", data, 80)
    xs, ys = [], []
    for i in range(count):
        for k in range(3):
            x, y, _z = struct.unpack_from("<3f", data, 84 + i * 50 + 12 + k * 12)
            xs.append(x)
            ys.append(y)
    assert (min(xs), max(xs)) == pytest.approx((-50, 0), abs=1e-3)
    assert (min(ys), max(ys)) == pytest.approx((0, 100), abs=1e-3)


def test_rotation_round_trip(tmp_path: Path) -> None:
    doc, bid = rotated_board()
    doc.boards[bid].rotation_axis = Axis.X
    path = tmp_path / "r.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert loaded.boards[bid].rotation_axis == Axis.X
    assert loaded.boards[bid].expressions["angle"] == "90"
