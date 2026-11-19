"""Bis Fläche ziehen: pull a face to a face of another part, linked by formula."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.model import transform
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.part import Axis
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.tools import ExtendTool, PickedFace

from .furniture import board_id, build_korpus, param_id


def _free_board(doc: Document) -> str:
    board = doc.add_board("Brett")
    for prop, text in {
        "length": "300",
        "width": "200",
        "thickness": "18",
        "x": "100",
        "z": "500",
    }.items():
        doc.set_expression(board.id, prop, text)
    return board.id


def test_face_formula_names_faces_and_pattern_copies() -> None:
    doc = build_korpus()
    evaluation = Evaluator().evaluate(doc)
    right = board_id(doc, "Seite_rechts")
    assert transform.face_formula(doc, evaluation, right, Axis.X, 782.0) == "Seite_rechts.x"
    assert transform.face_formula(doc, evaluation, right, Axis.X, 800.0) == "Seite_rechts.x2"
    assert transform.face_formula(doc, evaluation, right, Axis.X, 500.0) is None
    shelf = board_id(doc, "Fachboden")
    state = evaluation.boards[shelf]
    second = state.origin[2] + state.spacing
    assert transform.face_formula(doc, evaluation, shelf, Axis.Z, second) == (
        "Fachboden.z + 1 * Fachboden.spacing"
    )


def test_extend_end_and_start_follow_the_target() -> None:
    doc = build_korpus()
    brett = _free_board(doc)
    evaluation = Evaluator().evaluate(doc)
    transform.extend_to(doc, evaluation, brett, Axis.X, +1, "Seite_rechts.x", 782.0)
    assert doc.display_expression(brett, "length") == "Seite_rechts.x - 100"
    evaluation = Evaluator().evaluate(doc)
    transform.extend_to(doc, evaluation, brett, Axis.X, -1, "Seite_links.x2", 18.0)
    assert doc.display_expression(brett, "x") == "Seite_links.x2"
    state = Evaluator().evaluate(doc).boards[brett]
    assert state.origin[0] == pytest.approx(18.0)
    assert state.extent(Axis.X) == pytest.approx(764.0)
    # the cabinet gets wider: the board follows both sides
    doc.set_expression(param_id(doc, "B"), None, "1000")
    state = Evaluator().evaluate(doc).boards[brett]
    assert state.origin[0] == pytest.approx(18.0)
    assert state.extent(Axis.X) == pytest.approx(964.0)


def test_extend_refuses_zero_size() -> None:
    doc = build_korpus()
    brett = _free_board(doc)
    evaluation = Evaluator().evaluate(doc)
    with pytest.raises(ValueError):
        transform.extend_to(doc, evaluation, brett, Axis.X, +1, "Seite_links.x", 0.0)


def test_controller_falls_back_to_a_number_on_cycles(qtbot: QtBot) -> None:
    win = MainWindow()
    try:
        doc = build_korpus()
        win.controller._replace_document(doc, None)
        brett = _free_board(win.controller.doc)
        win.controller._replace_document(win.controller.doc, None)
        right = board_id(win.controller.doc, "Seite_rechts")
        assert win.controller.extend_board(brett, Axis.X, +1, right, 782.0) == "Seite_rechts.x"
        win.controller.undo_stack.undo()
        assert win.controller.doc.display_expression(brett, "length") == "300"
        # Seite_links (thickness along x) up to the bottom's end: the bottom
        # hangs on Seite_links.x2, so a link would be a cycle
        left = board_id(win.controller.doc, "Seite_links")
        bottom = board_id(win.controller.doc, "Boden")
        assert win.controller.extend_board(left, Axis.X, +1, bottom, 782.0) == ""
        assert win.controller.evaluation.boards[left].extent(Axis.X) == pytest.approx(782.0)
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()


def test_extend_tool_reports(qtbot: QtBot) -> None:
    win = MainWindow()
    try:
        doc = build_korpus()
        brett = _free_board(doc)
        win.controller._replace_document(doc, None)
        right = board_id(doc, "Seite_rechts")
        tool = ExtendTool(win.scene)
        win.scene.start_tool(tool)
        messages: list[str] = []
        win.scene.status_message.connect(messages.append)
        first = PickedFace(brett, 0, +1, 400.0, (400.0, 100.0, 509.0), None)
        second = PickedFace(right, 0, -1, 782.0, (782.0, 100.0, 509.0), None)
        tool.complete(first, second)
        assert "verknüpft (Seite_rechts.x)" in messages[-1]
        assert win.controller.doc.display_expression(brett, "length") == "Seite_rechts.x - 100"
        tool.complete(first, PickedFace(brett, 0, -1, 100.0, (100.0, 0.0, 0.0), None))
        assert "anderen Teils" in messages[-1]
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
