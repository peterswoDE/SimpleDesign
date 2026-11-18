"""Phase 3c joints: tenon, finger joint, dovetail."""

from __future__ import annotations

from pathlib import Path

import pytest

from simpledesign.core.geometry.mesh import Size, make_shape
from simpledesign.core.model.document import Document
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.machining import (
    Cutout,
    Feature,
    JointErrorCode,
    PartShape,
    Prism,
    local_shape,
)
from simpledesign.core.model.part import Orientation
from tests.furniture import board_id
from tests.test_joints import cut_summary, evaluate, korpus_joint

SIDE = 1800 * 400 * 18  # volume of a side of the korpus
FINGER = 400 / 23  # automatic finger width: 23 fingers on 400 mm


def volume(shape: PartShape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(make_shape(shape.size, shape.features), props)
    return float(props.Mass())


def solid_volume(size: Size, features: tuple[Feature, ...]) -> float:
    return volume(PartShape((0, 0, 0), size, features))


# ====================================================================== tenon
def test_tenon_too_long_for_the_side() -> None:
    doc, joint, _side, _bottom = korpus_joint(JointKind.TENON)  # 30 mm into 18 mm
    assert evaluate(doc).machining.errors == {joint: JointErrorCode.TOO_DEEP}


def test_tenon_and_mortise() -> None:
    doc, joint, side, bottom = korpus_joint(JointKind.TENON)
    doc.set_joint_param(joint, "length", 12)
    ev = evaluate(doc)
    assert not ev.machining.errors
    (mortise,) = ev.machining.parts[side][0].features
    assert isinstance(mortise, Cutout)
    # one third of the thickness, 10 mm shoulders, 1 mm deeper than the tenon
    assert mortise.lo == pytest.approx((5, 10, 6))
    assert mortise.hi == pytest.approx((18, 390, 12))
    shape = ev.machining.parts[bottom][0]
    assert shape.origin[0] == pytest.approx(6)
    assert shape.size == pytest.approx((776, 400, 18))
    assert len(shape.features) == 4 and shape.cut_operations == 1
    tenon = 12 * 380 * 6
    assert volume(shape) == pytest.approx(764 * 400 * 18 + tenon, rel=1e-6)
    assert volume(ev.machining.parts[side][0]) == pytest.approx(SIDE - 13 * 380 * 6, rel=1e-6)
    assert (1, 776, 400, 0, 1) in cut_summary(doc)


def test_tenon_settings() -> None:
    doc, joint, side, _bottom = korpus_joint(JointKind.TENON)
    doc.set_joint_param(joint, "length", 12)
    doc.set_joint_param(joint, "thickness", 8)
    doc.set_joint_param(joint, "shoulder", 0)
    (mortise,) = evaluate(doc).machining.parts[side][0].features
    assert isinstance(mortise, Cutout)
    assert (*mortise.lo[1:], *mortise.hi[1:]) == pytest.approx((0, 5, 400, 13))
    doc.set_joint_param(joint, "thickness", 18)
    assert evaluate(doc).machining.errors == {joint: JointErrorCode.INVALID_VALUE}
    doc.set_joint_param(joint, "thickness", 0)
    doc.set_joint_param(joint, "shoulder", 200)
    assert evaluate(doc).machining.errors == {joint: JointErrorCode.INVALID_VALUE}


# ====================================================================== finger joint
def test_finger_joint_automatic() -> None:
    doc, _joint, side, bottom = korpus_joint(JointKind.FINGER)
    ev = evaluate(doc)
    assert not ev.machining.errors
    sockets = ev.machining.parts[side][0].features
    shape = ev.machining.parts[bottom][0]
    assert len(sockets) == 11 and len(shape.features) == 12  # 23 fingers
    assert all(isinstance(f, Cutout) for f in (*sockets, *shape.features))
    # the bottom reaches through the side
    assert shape.origin[0] == pytest.approx(0)
    assert shape.size == pytest.approx((782, 400, 18))
    finger = FINGER * 18 * 18
    assert volume(ev.machining.parts[side][0]) == pytest.approx(SIDE - 11 * finger, rel=1e-6)
    assert volume(shape) == pytest.approx(764 * 400 * 18 + 11 * finger, rel=1e-6)


def test_finger_joint_fixed_count() -> None:
    doc, joint, side, bottom = korpus_joint(JointKind.FINGER)
    doc.set_joint_param(joint, "count", 4)  # side: 1st and 3rd, bottom: 2nd and 4th
    ev = evaluate(doc)
    sockets = ev.machining.parts[side][0].features
    gaps = ev.machining.parts[bottom][0].features
    assert sorted((f.lo[1], f.hi[1]) for f in sockets if isinstance(f, Cutout)) == [
        (100, 200),
        (300, 400),
    ]
    assert sorted((f.lo[1], f.hi[1]) for f in gaps if isinstance(f, Cutout)) == [
        (0, 100),
        (200, 300),
    ]
    doc.set_joint_param(joint, "count", 1)
    assert evaluate(doc).machining.errors == {joint: JointErrorCode.INVALID_VALUE}


def test_corner_joint_needs_the_main_face() -> None:
    doc = Document()
    a = doc.add_board("A")
    b = doc.add_board("B")
    doc.set_expression(b.id, "x", "A.x2")  # end to end: edge against edge
    joint = doc.add_joint(JointKind.FINGER, a.id, b.id)
    assert evaluate(doc).machining.errors == {joint.id: JointErrorCode.MAIN_EDGE}
    joint = doc.add_joint(JointKind.TENON, a.id, b.id)  # a tenon is fine at an edge
    doc.set_joint_param(joint.id, "length", 10)
    assert joint.id not in evaluate(doc).machining.errors


# ====================================================================== dovetail
def test_dovetail() -> None:
    doc, _joint, side, bottom = korpus_joint(JointKind.DOVETAIL)
    ev = evaluate(doc)
    assert not ev.machining.errors
    sockets = ev.machining.parts[side][0].features
    shape = ev.machining.parts[bottom][0]
    assert len(sockets) == 6 and len(shape.features) == 7  # 6 tails, 7 pins
    assert all(isinstance(f, Prism) for f in (*sockets, *shape.features))
    tail = sockets[0]
    assert isinstance(tail, Prism)
    # tails are wider at the far (outer) face than at the shoulder
    widths: dict[float, list[float]] = {round(p[0], 6): [] for p in tail.points}  # x -> y positions
    for p in tail.points:
        widths[round(p[0], 6)].append(p[1])
    outer, shoulder = (max(widths[x]) - min(widths[x]) for x in (0, 18))
    assert outer - shoulder == pytest.approx(2 * 18 / 6)
    assert shoulder == pytest.approx(400 / 6 - 8 - 6)
    tails = 6 * (outer + shoulder) / 2 * 18 * 18
    assert volume(ev.machining.parts[side][0]) == pytest.approx(SIDE - tails, rel=1e-6)
    assert volume(shape) == pytest.approx(764 * 400 * 18 + tails, rel=1e-6)
    assert shape.size == pytest.approx((782, 400, 18))


def test_dovetail_settings() -> None:
    doc, joint, side, _bottom = korpus_joint(JointKind.DOVETAIL)
    doc.set_joint_param(joint, "count", 3)
    assert len(evaluate(doc).machining.parts[side][0].features) == 3
    doc.set_joint_param(joint, "pin_width", 130)
    assert evaluate(doc).machining.errors == {joint: JointErrorCode.INVALID_VALUE}
    doc.set_joint_param(joint, "pin_width", 8)
    doc.set_joint_param(joint, "slope", 0)
    assert evaluate(doc).machining.errors == {joint: JointErrorCode.INVALID_VALUE}


def test_dovetail_signature_and_export(tmp_path: Path) -> None:
    from simpledesign.core.io.export import export

    doc, _joint, _side, bottom = korpus_joint(JointKind.DOVETAIL)
    right = board_id(doc, "Seite_rechts")
    doc.add_joint(JointKind.DOVETAIL, right, bottom)
    ev = evaluate(doc)
    size, signature = local_shape(ev.machining.parts[bottom][0], Orientation.XY)
    assert size == (800, 400, 18)
    assert len(signature) == 14
    assert (1, 800, 400, 0, 2) in cut_summary(doc)
    for suffix in (".step", ".stl"):
        path = tmp_path / f"k{suffix}"
        export(doc, ev, path)
        assert path.stat().st_size > 0
