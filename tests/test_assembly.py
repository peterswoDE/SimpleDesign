"""Exploded view and assembly instructions (Montageanleitung)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtPdf import QPdfDocument
from pytestqt.qtbot import QtBot

from simpledesign.core.drawing.assembly import assembly_steps, step_pictures
from simpledesign.core.geometry.explode import explode_offsets
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.ui.assembly_pdf import step_lines, step_title, write_assembly_pdf
from simpledesign.ui.main_window import MainWindow

from .furniture import board_id, build_korpus


def _joined() -> tuple[Document, Evaluation]:
    doc = build_korpus()
    for side in ("Seite_links", "Seite_rechts"):
        for part in ("Boden", "Deckel", "Fachboden"):
            doc.add_joint(JointKind.DOWEL, board_id(doc, side), board_id(doc, part))
    return doc, Evaluator().evaluate(doc)


def test_explode_offsets_point_away_from_the_centre() -> None:
    doc, ev = _joined()
    assert all(d == (0.0, 0.0, 0.0) for ds in explode_offsets(doc, ev, 0).values() for d in ds)
    offsets = explode_offsets(doc, ev, 1.0)
    assert offsets[board_id(doc, "Seite_links")][0][0] < 0
    assert offsets[board_id(doc, "Seite_rechts")][0][0] > 0
    assert offsets[board_id(doc, "Deckel")][0][2] > 0
    assert offsets[board_id(doc, "Boden")][0][2] < 0
    shelves = [d[2] for d in offsets[board_id(doc, "Fachboden")]]
    assert shelves == sorted(shelves) and shelves[0] < 0 < shelves[-1]
    # twice the factor, twice the way
    double = explode_offsets(doc, ev, 2.0)[board_id(doc, "Deckel")][0][2]
    assert double == pytest.approx(2 * offsets[board_id(doc, "Deckel")][0][2])


def test_assembly_order_follows_the_joints() -> None:
    doc, ev = _joined()
    steps = assembly_steps(doc, ev)
    names = [doc.boards[s.board_id].name for s in steps]
    assert len(names) == len(doc.boards)
    assert names[0].startswith("Seite")
    assert names[-1] == "Rückwand"  # no joint: last
    assert not steps[0].connections and not steps[-1].connections
    placed: set[str] = set()
    for step in steps:
        assert all(c.partner in placed for c in step.connections)
        placed.add(step.board_id)
    shelf = next(s for s in steps if s.board_id == board_id(doc, "Fachboden"))
    assert shelf.copies == 4
    assert step_title(doc, 3, shelf) == "Schritt 3: Fachboden (4×)"
    assert {c.kind for c in shelf.connections} == {JointKind.DOWEL}
    assert any(line.startswith("Dübel an Seite") for line in step_lines(doc, shelf))


def test_step_pictures_and_pdf(tmp_path: Path, qtbot: QtBot) -> None:
    doc, ev = _joined()
    steps = assembly_steps(doc, ev)[:3]
    pictures = step_pictures(doc, ev, steps)
    assert not pictures[0].old and pictures[0].new
    assert pictures[1].old and pictures[1].new
    path = tmp_path / "montage.pdf"
    pages = write_assembly_pdf(path, doc, ev, "Regal", steps, pictures)
    document = QPdfDocument()
    document.load(str(path))
    assert document.pageCount() == pages == 2


def test_exploded_view_in_the_window(tmp_path: Path, qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        doc = build_korpus()
        win.controller._replace_document(doc, None)
        top = board_id(doc, "Deckel")
        win.controller.set_selection([top])
        ((face, _edge),) = win.scene._board_actors[top]
        z = face.GetPosition()[2]
        win.action_explode.trigger()
        assert win.scene.exploded and win.explode_box.isVisibleTo(win)
        assert win.scene.gizmo_origin() is None
        lifted = face.GetPosition()[2]
        assert lifted > z
        win.explode_slider.setValue(win.explode_slider.maximum())
        assert face.GetPosition()[2] > lifted
        # selecting still works, moving does not
        win.scene._press_board, win.scene._press_point = top, (400.0, 200.0, 1790.0)
        assert win.scene._start_part_drag(False) is None
        win.action_explode.trigger()
        assert not win.scene.exploded and face.GetPosition()[2] == pytest.approx(z)
        assert win.scene.gizmo_origin() is not None
        path = tmp_path / "m.pdf"
        assert win.export_assembly_to(path)
        assert path.stat().st_size > 1000
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
