"""Splitting parts where another part passes through them."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QKeySequence
from pytestqt.qtbot import QtBot

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.part import Orientation
from simpledesign.core.model.split import split_at

from .furniture import board_id, build_korpus


def _full_divider(doc: Document) -> str:
    """A divider from bottom to top, through all shelves."""
    board = doc.add_board("Trennwand", orientation=Orientation.ZY)
    for prop, text in {
        "length": "Deckel.z - Boden.z2",
        "width": "T - 20",
        "thickness": "t",
        "x": "391",
        "z": "Boden.z2",
    }.items():
        doc.set_expression(board.id, prop, text)
    doc.add_joint(JointKind.DOWEL, board_id(doc, "Boden"), board.id)
    doc.add_joint(JointKind.DOWEL, board_id(doc, "Deckel"), board.id)
    return board.id


def _korpus() -> Document:
    doc = build_korpus()
    shelf = board_id(doc, "Fachboden")
    doc.add_joint(JointKind.DOWEL, board_id(doc, "Seite_links"), shelf)
    doc.add_joint(JointKind.DOWEL, board_id(doc, "Seite_rechts"), shelf)
    return doc


def test_shelves_split_at_divider() -> None:
    doc = _korpus()
    divider = _full_divider(doc)
    shelf = board_id(doc, "Fachboden")
    (right,) = split_at(doc, Evaluator().evaluate(doc), [divider])
    ev = Evaluator().evaluate(doc)
    assert ev.boards[shelf].x == pytest.approx(18)
    assert ev.boards[shelf].length == pytest.approx(391 - 18)
    assert ev.boards[right].x == pytest.approx(409)
    assert ev.boards[right].length == pytest.approx(782 - 409)
    assert ev.boards[right].count == 4  # the pattern goes with the piece
    # joints: each side to its piece, the divider to both
    left_side, right_side = board_id(doc, "Seite_links"), board_id(doc, "Seite_rechts")
    assert {j.main_id for j in doc.joints_of(shelf)} == {left_side, divider}
    assert {j.main_id for j in doc.joints_of(right)} == {right_side, divider}
    # the pieces follow the divider
    doc.set_expression(divider, "x", "300")
    ev = Evaluator().evaluate(doc)
    assert ev.boards[shelf].length == pytest.approx(300 - 18)
    assert ev.boards[right].x == pytest.approx(318)
    assert ev.boards[right].length == pytest.approx(782 - 318)


def test_divider_split_at_shelves() -> None:
    doc = _korpus()
    divider = _full_divider(doc)
    shelf = board_id(doc, "Fachboden")
    pieces = split_at(doc, Evaluator().evaluate(doc), [shelf])
    assert len(pieces) == 4  # 4 shelves: 5 pieces
    ev = Evaluator().evaluate(doc)
    all_pieces = [divider, *pieces]
    heights = sum(ev.boards[p].length for p in all_pieces)
    assert heights == pytest.approx(1800 - 2 * 18 - 4 * 18)
    assert ev.boards[divider].z == pytest.approx(18)
    for piece in all_pieces:
        assert shelf in {j.main_id for j in doc.joints_of(piece)}
    # the shelves themselves are not touched
    assert board_id(doc, "Fachboden") == shelf and ev.boards[shelf].length == pytest.approx(764)


def test_nothing_to_split() -> None:
    doc = _korpus()
    shelf = board_id(doc, "Fachboden")
    side = board_id(doc, "Seite_links")
    count = len(doc.boards)
    assert split_at(doc, Evaluator().evaluate(doc), [side, shelf]) == []
    assert len(doc.boards) == count


def test_split_action_in_one_step(qtbot: QtBot) -> None:
    from simpledesign.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    c = window.controller
    c._replace_document(_korpus(), None)
    divider = c.execute("Trennwand", _full_divider)
    count, steps = len(c.doc.boards), c.undo_stack.count()
    c.set_selection([divider])
    window.action_split.trigger()
    assert len(c.doc.boards) == count + 1
    assert c.undo_stack.count() == steps + 1
    window.action_split.trigger()  # nothing left to split: no new step
    assert c.undo_stack.count() == steps + 1
    c.undo_stack.undo()
    assert len(c.doc.boards) == count
    c.undo_stack.setClean()


def test_toolbar_labels_show_shortcuts(qtbot: QtBot) -> None:
    from simpledesign.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    assert {"file", "edit", "insert", "fit", "tools", "view"} <= set(window.toolbars)
    action = window.action_measure
    assert action in window.toolbars["tools"].actions()
    assert action.iconText() == "Messen (M)"
    assert action.toolTip().endswith("(M)")
    assert window.action_split.iconText() == "Auftrennen (A)"
    assert window.action_insert_back.iconText() == "Rückwand"  # no shortcut
    action.setShortcut(QKeySequence("Ctrl+M"))  # e.g. from the shortcuts dialog
    assert action.iconText() == "Messen (Strg+M)"
    assert action.toolTip().count("(") == 1
    assert action.text() == "&Messen"  # menus keep their own text
