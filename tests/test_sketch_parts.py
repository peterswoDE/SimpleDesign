"""Phase 5: sketched parts (profile boards, cut-outs) in the document."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.machining import Extrusion
from simpledesign.core.model.part import Face, Orientation, SketchCut
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.sketch import model
from simpledesign.core.sketch.model import ConstraintKind as K
from simpledesign.core.sketch.model import Sketch


def rect_sketch(origin: bool = True) -> tuple[Sketch, dict[str, str]]:
    """Rectangle (width, height dimensions) with a circular hole of radius ``r``."""
    s = Sketch()
    lines = s.add_rectangle((10, 10), (250, 180))
    if origin:
        s.add_constraint(K.COINCIDENT, (lines[0].start, model.ORIGIN))
    w = s.add_constraint(K.DISTANCE, (lines[0].id,))
    h = s.add_constraint(K.DISTANCE, (lines[1].id,))
    return s, {w.id: "Breite", h.id: "200"}


def with_hole(s: Sketch, formulas: dict[str, str]) -> None:
    c = s.add_point(100, 100)
    circle = s.add_circle(c.id, 20)
    r = s.add_constraint(K.RADIUS, (circle.id,))
    formulas[r.id] = "25"


def profile_doc() -> tuple[Document, str]:
    doc = Document()
    doc.add_parameter("Breite", "300")  # referenced by name in the formulas
    board = doc.add_board()
    s, formulas = rect_sketch()
    with_hole(s, formulas)
    doc.set_profile(board.id, s, formulas)
    return doc, board.id


def evaluate(doc: Document) -> Evaluation:
    return Evaluator().evaluate(doc)


def test_profile_drives_length_and_width() -> None:
    doc, board_id = profile_doc()
    ev = evaluate(doc)
    assert ev.value(board_id, "length") == pytest.approx(300, abs=1e-4)
    assert ev.value(board_id, "width") == pytest.approx(200, abs=1e-4)
    solution = ev.sketches[board_id]
    assert solution.error is None
    assert len(solution.regions) == 1 and len(solution.regions[0].holes) == 1
    # Parameter change updates the part.
    param = next(p for p in doc.parameters.values() if p.name == "Breite")
    doc.set_expression(param.id, None, "400")
    assert evaluate(doc).value(board_id, "length") == pytest.approx(400, abs=1e-4)


def test_profile_outline_is_an_extrusion_and_meshes() -> None:
    doc, board_id = profile_doc()
    ev = evaluate(doc)
    (shape,) = ev.machining.parts[board_id]
    outlines = [f for f in shape.features if isinstance(f, Extrusion) and not f.cut]
    assert len(outlines) == 1
    from simpledesign.core.geometry.mesh import part_mesh

    mesh = part_mesh(shape.size, shape.features)
    lo = mesh.points.min(axis=0)
    hi = mesh.points.max(axis=0)
    assert hi - lo == pytest.approx(shape.size, abs=1e-3)


def test_unsolvable_profile_keeps_last_size() -> None:
    doc, board_id = profile_doc()
    evaluator = Evaluator()
    evaluator.evaluate(doc)
    board = doc.boards[board_id]
    assert board.profile is not None
    lines = [e for e in board.profile.entities.values() if isinstance(e, model.Line)]
    extra = board.profile.add_constraint(K.DISTANCE, (lines[2].id,))  # opposite side
    doc.set_expression(board_id, extra.formula_key, "123")
    ev = evaluator.evaluate(doc)
    error = ev.sketches[board_id].error
    assert error is not None and error.code == ErrorCode.SKETCH_UNSOLVABLE
    assert ev.value(board_id, "length") == pytest.approx(300, abs=1e-4)


def test_open_profile_reports_problem() -> None:
    doc = Document()
    board = doc.add_board()
    s = Sketch()
    a, b = s.add_point(0, 0), s.add_point(100, 0)
    s.add_line(a.id, b.id)
    doc.set_profile(board.id, s, {})
    error = evaluate(doc).sketches[board.id].error
    assert error is not None and error.code == ErrorCode.PROFILE_INVALID


def test_dimension_must_be_positive() -> None:
    doc, board_id = profile_doc()
    board = doc.boards[board_id]
    assert board.profile is not None
    key = board.profile.dimensions()[1].formula_key
    doc.set_expression(board_id, key, "-5")
    assert evaluate(doc).error(board_id, key) is not None


def test_cut_adds_feature_and_mesh_volume_shrinks() -> None:
    doc = Document()
    board = doc.add_board()
    s, formulas = rect_sketch(origin=False)
    formulas = {cid: "50" for cid in formulas}
    cut = SketchCut("c1", s, Face.TOP)
    doc.set_cut(board.id, cut, formulas, "5")
    ev = evaluate(doc)
    assert ev.sketches[f"{board.id}/c1"].error is None
    (shape,) = ev.machining.parts[board.id]
    cuts = [f for f in shape.features if isinstance(f, Extrusion) and f.cut]
    assert len(cuts) == 1 and shape.cut_operations == 1
    assert cuts[0].length == pytest.approx(5)
    from simpledesign.core.geometry.mesh import part_mesh

    mesh = part_mesh(shape.size, shape.features)
    assert len(mesh.faces) > 12 * 4  # more than a plain box

    doc.delete_cut(board.id, "c1")
    assert not doc.boards[board.id].cuts
    assert not any(k.startswith(("d.", "depth.")) for k in doc.boards[board.id].expressions)


def test_bad_depth_formula_rejected() -> None:
    doc = Document()
    board = doc.add_board()
    s, formulas = rect_sketch(origin=False)
    with pytest.raises(FormulaError):
        doc.set_cut(board.id, SketchCut("c1", s), formulas, "Unbekannt + 1")


def test_sdz_round_trip_and_duplicate() -> None:
    doc, board_id = profile_doc()
    s, formulas = rect_sketch(origin=False)
    doc.set_cut(board_id, SketchCut("c1", s, Face.BOTTOM, through=True), formulas, "3")
    again = sdz.from_dict(sdz.to_dict(doc))
    assert sdz.to_dict(again) == sdz.to_dict(doc)
    ev = evaluate(again)
    assert ev.value(board_id, "length") == pytest.approx(300, abs=1e-4)

    (copy_id,) = doc.duplicate([board_id])
    copy = doc.boards[copy_id]
    assert copy.profile is not None and copy.profile is not doc.boards[board_id].profile
    assert len(copy.cuts) == 1
    ev = evaluate(doc)
    assert ev.value(copy_id, "width") == pytest.approx(200, abs=1e-4)


def test_remove_profile_freezes_size() -> None:
    doc, board_id = profile_doc()
    ev = evaluate(doc)
    doc.set_profile(board_id, None, values=ev.values)
    ev = evaluate(doc)
    assert doc.boards[board_id].profile is None
    assert ev.value(board_id, "length") == pytest.approx(300, abs=1e-4)


def test_controller_profile_board_undo(qtbot: QtBot, tmp_path: Path) -> None:
    controller = Controller(recovery_dir=tmp_path / "recovery")
    s, formulas = rect_sketch()
    formulas = {cid: "120" for cid in formulas}
    board_id = controller.add_profile_board(Orientation.XZ, s, formulas)
    assert controller.evaluation.value(board_id, "length") == pytest.approx(120, abs=1e-4)
    s.add_point(1, 1)  # the stored sketch is a copy
    stored = controller.doc.boards[board_id].profile
    assert stored is not None and len(stored.points) != len(s.points)
    controller.undo_stack.undo()
    assert board_id not in controller.doc.boards
