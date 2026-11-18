"""Joints: contact, drilling patterns, grooves/rabbets, errors, persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from simpledesign.core.io import sdz
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.machining import (
    Cutout,
    Hole,
    JointErrorCode,
    hole_positions,
    local_shape,
)
from simpledesign.core.model.part import Orientation
from tests.furniture import board_id, build_korpus


def evaluate(doc: Document) -> Evaluation:
    return Evaluator().evaluate(doc)


def korpus_joint(kind: JointKind) -> tuple[Document, str, str, str]:
    """Left side (main) and bottom (attached) with a joint."""
    doc = build_korpus()
    side, bottom = board_id(doc, "Seite_links"), board_id(doc, "Boden")
    joint = doc.add_joint(kind, side, bottom)
    return doc, joint.id, side, bottom


# ====================================================================== rules
@pytest.mark.parametrize(
    ("length", "count", "expected"),
    [
        (400, 0, [50, 200, 350]),  # 300 usable / 150 -> 3 holes
        (200, 0, [50, 150]),  # at least two
        (80, 0, [40]),  # no room for edge distances -> one hole in the middle
        (400, 2, [50, 350]),  # fixed count
        (60, 2, [15, 45]),  # fixed count, edge distance does not fit
    ],
)
def test_hole_positions(length: float, count: int, expected: list[float]) -> None:
    assert hole_positions(length, 50, 150, count) == pytest.approx(expected)


# ====================================================================== dowels and screws
def test_dowels_drill_both_parts() -> None:
    doc, _joint, side, bottom = korpus_joint(JointKind.DOWEL)
    ev = evaluate(doc)
    assert not ev.machining.errors
    side_holes = ev.machining.parts[side][0].features
    bottom_holes = ev.machining.parts[bottom][0].features
    assert len(side_holes) == len(bottom_holes) == 3
    hole = side_holes[0]
    assert isinstance(hole, Hole)
    # drilled into the side from its inner face (x = 18), towards -x
    assert (hole.axis, hole.direction, hole.depth, hole.diameter) == (0, -1, 12, 8)
    assert hole.center[0] == pytest.approx(18) and hole.center[2] == pytest.approx(9)
    pin = bottom_holes[0]
    assert isinstance(pin, Hole)
    assert (pin.direction, pin.depth) == (1, 29)
    assert ev.machining.hardware[0].quantity == 3
    # the rough size of the bottom stays the visible size
    assert ev.machining.parts[bottom][0].size == pytest.approx((764, 400, 18))


def test_dowel_count_and_hardware_sum() -> None:
    doc, joint, _side, bottom = korpus_joint(JointKind.DOWEL)
    doc.set_joint_param(joint, "count", 4)
    right = board_id(doc, "Seite_rechts")
    doc.add_joint(JointKind.DOWEL, right, bottom)
    ev = evaluate(doc)
    (hardware,) = ev.machining.hardware
    assert (hardware.kind, hardware.diameter, hardware.length) == (JointKind.DOWEL, 8, 40)
    assert hardware.quantity == 4 + 3


def test_screws_go_through_main_part() -> None:
    doc, _joint, side, bottom = korpus_joint(JointKind.SCREW)
    ev = evaluate(doc)
    hole = ev.machining.parts[side][0].features[0]
    pilot = ev.machining.parts[bottom][0].features[0]
    assert isinstance(hole, Hole) and isinstance(pilot, Hole)
    assert (hole.depth, hole.diameter) == (18, 4.5)
    assert (pilot.depth, pilot.diameter) == (32, 3)
    assert ev.machining.hardware[0].quantity == 3  # 300 usable, at most 200 apart


def test_pattern_copies_get_one_set_each() -> None:
    doc = build_korpus()
    side, shelf = board_id(doc, "Seite_links"), board_id(doc, "Fachboden")
    doc.add_joint(JointKind.DOWEL, side, shelf)
    ev = evaluate(doc)
    assert not ev.machining.errors
    assert len(ev.machining.parts[side][0].features) == 4 * 3  # 4 shelves, 380 deep
    assert all(len(p.features) == 3 for p in ev.machining.parts[shelf])
    assert ev.machining.hardware[0].quantity == 12


# ====================================================================== groove / rabbet
def test_groove_extends_the_attached_part() -> None:
    doc, _joint, side, bottom = korpus_joint(JointKind.GROOVE)
    ev = evaluate(doc)
    assert not ev.machining.errors
    (cut,) = ev.machining.parts[side][0].features
    assert isinstance(cut, Cutout)
    # widened by the play, but never beyond the main part
    assert cut.lo == pytest.approx((8, 0, 0))
    assert cut.hi == pytest.approx((18, 400, 18.25))
    shape = ev.machining.parts[bottom][0]
    assert shape.origin[0] == pytest.approx(18 - 9.5)
    assert shape.size == pytest.approx((773.5, 400, 18))
    assert ev.machining.hardware == []


def test_rabbet_is_open_to_the_outside() -> None:
    doc = build_korpus()
    side, back = board_id(doc, "Seite_links"), board_id(doc, "Rückwand")
    # make the back panel sit between the sides, touching the left side
    doc.set_expression(back, "x", "18")
    doc.set_expression(back, "length", "B - 36")
    doc.set_expression(back, "y", "T - 3")
    doc.add_joint(JointKind.RABBET, side, back)
    ev = evaluate(doc)
    assert not ev.machining.errors
    (cut,) = ev.machining.parts[side][0].features
    assert isinstance(cut, Cutout)
    assert cut.hi[1] == pytest.approx(400)  # open to the back face of the side
    assert cut.lo[1] == pytest.approx(397 - 0.25)


# ====================================================================== errors
def test_no_contact() -> None:
    doc = build_korpus()
    left, right = board_id(doc, "Seite_links"), board_id(doc, "Seite_rechts")
    joint = doc.add_joint(JointKind.DOWEL, left, right)
    ev = evaluate(doc)
    assert ev.machining.errors == {joint.id: JointErrorCode.NO_CONTACT}
    assert ev.machining.parts[left][0].features == ()


@pytest.mark.parametrize(
    ("kind", "param", "value", "code"),
    [
        (JointKind.DOWEL, "depth_main", 20, JointErrorCode.TOO_DEEP),
        (JointKind.DOWEL, "depth_main", 50, JointErrorCode.INVALID_VALUE),
        (JointKind.DOWEL, "diameter", 0, JointErrorCode.INVALID_VALUE),
        (JointKind.SCREW, "length", 20, JointErrorCode.SCREW_TOO_SHORT),
        (JointKind.GROOVE, "depth", 18, JointErrorCode.TOO_DEEP),
        (JointKind.GROOVE, "play", 12, JointErrorCode.INVALID_VALUE),
    ],
)
def test_joint_errors(kind: JointKind, param: str, value: float, code: JointErrorCode) -> None:
    doc, joint, side, _bottom = korpus_joint(kind)
    doc.set_joint_param(joint, param, value)
    ev = evaluate(doc)
    assert ev.machining.errors == {joint: code}
    assert ev.machining.parts[side][0].features == ()


def test_groove_takes_an_upright_part() -> None:
    doc = Document()
    floor = doc.add_board("Boden")
    wall = doc.add_board("Wand", orientation=Orientation.ZY)
    doc.set_expression(wall.id, "x", "100")
    doc.set_expression(wall.id, "z", "Boden.z2")
    joint = doc.add_joint(JointKind.GROOVE, wall.id, floor.id)
    ev = evaluate(doc)
    # the floor touches the wall with its face -> cannot go into a groove
    assert ev.machining.errors == {joint.id: JointErrorCode.FACE_CONTACT}
    doc.swap_joint(joint.id)
    ev = evaluate(doc)
    assert not ev.machining.errors
    floor_top = ev.boards[floor.id].thickness
    assert ev.machining.parts[wall.id][0].origin[2] == pytest.approx(floor_top - 9.5)


def test_face_contact_is_reported() -> None:
    doc = Document()
    a = doc.add_board("A")
    b = doc.add_board("B")
    doc.set_expression(b.id, "z", "A.z2")
    joint = doc.add_joint(JointKind.GROOVE, a.id, b.id)
    assert evaluate(doc).machining.errors == {joint.id: JointErrorCode.FACE_CONTACT}


# ====================================================================== document
def test_delete_and_duplicate_keep_joints_consistent() -> None:
    doc, joint, side, bottom = korpus_joint(JointKind.DOWEL)
    copies = doc.duplicate([side, bottom])
    assert len(doc.joints) == 2
    twin = next(j for j in doc.joints.values() if j.id != joint)
    assert (twin.main_id, twin.attached_id) == tuple(copies)
    doc.duplicate([side])  # only one end copied -> no new joint
    assert len(doc.joints) == 2
    doc.delete([bottom], evaluate(doc).values)
    assert list(doc.joints) == [twin.id]


def test_joint_needs_two_boards() -> None:
    doc = build_korpus()
    side = board_id(doc, "Seite_links")
    with pytest.raises(ValueError):
        doc.add_joint(JointKind.DOWEL, side, side)
    with pytest.raises(KeyError):
        doc.add_joint(JointKind.DOWEL, side, "nope")
    joint = doc.add_joint(JointKind.DOWEL, side, board_id(doc, "Boden"))
    with pytest.raises(KeyError):
        doc.set_joint_param(joint.id, "depth", 3)


def test_sdz_roundtrip(tmp_path: Path) -> None:
    doc, joint, _side, _bottom = korpus_joint(JointKind.GROOVE)
    doc.set_joint_param(joint, "depth", 8)
    path = tmp_path / "k.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert loaded.joints == doc.joints


# ====================================================================== cut list signature
def test_local_shape_is_independent_of_position() -> None:
    doc = build_korpus()
    side, right, bottom = (board_id(doc, n) for n in ("Seite_links", "Seite_rechts", "Boden"))
    doc.add_joint(JointKind.DOWEL, side, bottom)
    doc.add_joint(JointKind.DOWEL, right, bottom)
    ev = evaluate(doc)
    left_size, left_sig = local_shape(ev.machining.parts[side][0], Orientation.ZY)
    right_size, right_sig = local_shape(ev.machining.parts[right][0], Orientation.ZY)
    assert left_size == right_size == (1800, 400, 18)
    # mirrored parts: the holes face the other way, so the machining differs
    assert left_sig != right_sig


# ====================================================================== geometry
def test_machined_mesh_and_export(tmp_path: Path) -> None:
    from simpledesign.core.geometry.mesh import box_mesh, part_mesh
    from simpledesign.core.io.export import export

    doc, _joint, side, _bottom = korpus_joint(JointKind.DOWEL)
    ev = evaluate(doc)
    shape = ev.machining.parts[side][0]
    plain = box_mesh(shape.size)
    drilled = part_mesh(shape.size, shape.features)
    assert len(drilled.faces) > len(plain.faces)
    # the holes stay inside the part
    assert drilled.points.min(axis=0) == pytest.approx((0, 0, 0))
    assert drilled.points.max(axis=0) == pytest.approx(shape.size)
    for suffix in (".step", ".stl", ".obj"):
        path = tmp_path / f"k{suffix}"
        export(doc, ev, path)
        assert path.stat().st_size > 0


# ====================================================================== cut list
def cut_summary(doc: Document) -> list[tuple[int, float, float, int, int]]:
    from simpledesign.core.cutlist.cutlist import build_cutlist

    rows = build_cutlist(doc, evaluate(doc))
    return [(r.quantity, r.length, r.width, r.holes, r.cutouts) for r in rows if r.thickness == 18]


def test_cutlist_splits_by_machining_and_uses_rough_size() -> None:
    doc, _joint, _side, _bottom = korpus_joint(JointKind.GROOVE)
    rows = cut_summary(doc)
    # the left side has a groove now, the right side not; the bottom is longer
    assert (1, 1800, 400, 0, 1) in rows
    assert (1, 1800, 400, 0, 0) in rows
    assert (1, 773.5, 400, 0, 0) in rows
    assert (1, 764, 400, 0, 0) in rows  # the top


def test_equal_machining_is_merged() -> None:
    doc = build_korpus()
    side, bottom, top = (board_id(doc, n) for n in ("Seite_links", "Boden", "Deckel"))
    doc.add_joint(JointKind.DOWEL, side, bottom)
    doc.add_joint(JointKind.DOWEL, side, top)
    rows = cut_summary(doc)
    assert (2, 764, 400, 3, 0) in rows  # bottom and top: same holes
    assert (1, 1800, 400, 6, 0) in rows


def test_optimizer_uses_rough_size() -> None:
    from simpledesign.core.cutlist.optimize import pieces_by_material

    doc, _joint, _side, bottom = korpus_joint(JointKind.GROOVE)
    pieces = pieces_by_material(doc, evaluate(doc))
    assert any(p.length == pytest.approx(773.5) for p in pieces[doc.boards[bottom].material_id])
