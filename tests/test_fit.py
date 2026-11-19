"""Resize, rotate about the centre, fit new boards into compartments."""

from __future__ import annotations

import pytest

from simpledesign.core.model import fit, transform
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.part import Axis, Orientation

from .furniture import board_id, build_korpus


def _eval(doc: Document) -> Evaluation:
    return Evaluator().evaluate(doc)


def _box(doc: Document, name: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    state = _eval(doc).boards[board_id(doc, name)]
    lo = state.origin
    return lo, tuple(lo[i] + state.size[i] for i in range(3))


# ---------------------------------------------------------------- resize
def test_resize_end_face_keeps_formula() -> None:
    doc = build_korpus()
    bottom = board_id(doc, "Boden")
    transform.resize(doc, _eval(doc), bottom, Axis.Y, 1, -50)
    assert doc.display_expression(bottom, "width") == "T - 50"
    assert _box(doc, "Boden")[1][1] == pytest.approx(350)


def test_resize_start_face_moves_position() -> None:
    doc = build_korpus()
    left = board_id(doc, "Seite_links")
    transform.resize(doc, _eval(doc), left, Axis.Z, -1, 100)
    lo, hi = _box(doc, "Seite_links")
    assert lo[2] == pytest.approx(100)
    assert hi[2] == pytest.approx(1800)  # opposite face stays


def test_resize_rejects_too_small() -> None:
    doc = build_korpus()
    with pytest.raises(ValueError):
        transform.resize(doc, _eval(doc), board_id(doc, "Boden"), Axis.Z, 1, -50)


# ---------------------------------------------------------------- rotate
def test_rotate_keeps_centre() -> None:
    doc = Document()
    board = doc.add_board("Brett")
    for prop, text in {"length": "600", "width": "300", "thickness": "18"}.items():
        doc.set_expression(board.id, prop, text)
    before = _eval(doc).boards[board.id]
    centre = [before.origin[i] + before.size[i] / 2 for i in range(3)]
    transform.rotate(doc, _eval(doc), board.id, Axis.Z, 30)
    transform.rotate(doc, _eval(doc), board.id, Axis.Z, 15)
    after = _eval(doc).boards[board.id]
    assert after.angle == pytest.approx(45)
    shift = transform.rotation_shift(after.size, 2, 0, 45)
    corner = [centre[i] - before.size[i] / 2 + shift[i] for i in range(3)]
    assert list(after.origin) == pytest.approx(corner, abs=1e-2)
    with pytest.raises(ValueError):
        transform.rotate(doc, _eval(doc), board.id, Axis.X, 10)


# ---------------------------------------------------------------- compartments
def _korpus_without_shelves() -> Document:
    doc = build_korpus()
    doc.delete([board_id(doc, "Fachboden")], {})
    return doc


def test_compartment_between_carcass_parts() -> None:
    doc = _korpus_without_shelves()
    # cursor on the top face of the bottom, middle of the cabinet
    comp = fit.compartment(doc, _eval(doc), (400, 200, 18), normal=2, sign=1)
    assert comp is not None
    assert comp.lo[0].formula == "Seite_links.x2"
    assert comp.hi[0].formula == "Seite_rechts.x"
    assert comp.lo[2].formula == "Boden.z2"
    assert comp.hi[2].formula == "Deckel.z"
    assert comp.size(1) == pytest.approx(400)  # open front: depth of the sides


def test_insert_divider_centred_and_linked() -> None:
    doc = _korpus_without_shelves()
    evaluation = _eval(doc)
    comp = fit.compartment(doc, evaluation, (400, 200, 18), normal=2, sign=1)
    assert comp is not None
    plan = fit.plan_insert(doc, evaluation, comp, fit.InsertKind.DIVIDER, 0, 405, 18, snap=20)
    assert plan is not None and plan.centred
    assert plan.orientation == Orientation.ZY
    new = fit.create(doc, plan, None, doc.boards[board_id(doc, "Boden")].material_id, "t")
    lo, hi = _box(doc, doc.boards[new].name)
    assert lo[0] == pytest.approx(391) and hi[0] == pytest.approx(409)
    assert lo[2] == pytest.approx(18) and hi[2] == pytest.approx(1782)
    assert {doc.boards[j.main_id].name for j in doc.joints_of(new)} == {"Boden", "Deckel"}
    # wider cabinet: the divider follows
    doc.set_expression(next(p.id for p in doc.parameters.values() if p.name == "B"), None, "1000")
    lo, hi = _box(doc, doc.boards[new].name)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(500)


def test_insert_shelf_at_typed_gap() -> None:
    doc = _korpus_without_shelves()
    evaluation = _eval(doc)
    comp = fit.compartment(doc, evaluation, (18, 200, 900), normal=0, sign=1)
    assert comp is not None
    plan = fit.plan_insert(doc, evaluation, comp, fit.InsertKind.SHELF, 0, 0, 18, typed=300)
    assert plan is not None
    assert plan.orientation == Orientation.XY
    new = fit.create(doc, plan, None, doc.boards[board_id(doc, "Boden")].material_id, "t")
    lo, hi = _box(doc, doc.boards[new].name)
    assert lo[2] == pytest.approx(318)
    assert hi[0] - lo[0] == pytest.approx(764)
    assert {doc.boards[j.main_id].name for j in doc.joints_of(new)} == {
        "Seite_links",
        "Seite_rechts",
    }


def test_seat_back_panel_in_groove() -> None:
    doc = _korpus_without_shelves()
    back = board_id(doc, "Rückwand")
    # move the back into the carcass first
    doc.set_expression(back, "y", "T - 20")
    groove = fit.GrooveSettings(depth=8, play=0.5, distance=10)
    around = fit.seat_in_groove(doc, _eval(doc), back, groove)
    names = {doc.boards[i].name for i in around}
    assert names == {"Seite_links", "Seite_rechts", "Boden", "Deckel"}
    lo, hi = _box(doc, "Rückwand")
    assert hi[1] == pytest.approx(390)  # 10 mm from the back edge
    assert lo[0] == pytest.approx(18) and hi[0] == pytest.approx(782)
    joints = doc.joints_of(back)
    assert len(joints) == 4 and all(j.kind == JointKind.GROOVE for j in joints)
    assert all(j.params["depth"] == 8 for j in joints)


def test_seat_rejects_free_panel() -> None:
    doc = Document()
    panel = doc.add_board("Platte", orientation=Orientation.XZ)
    with pytest.raises(ValueError):
        fit.seat_in_groove(doc, _eval(doc), panel.id, fit.GrooveSettings())
