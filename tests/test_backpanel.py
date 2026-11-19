"""Back panels: distance to the rear edge, parts stop at them, parts through them."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.model import backpanel, fit
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.part import Orientation

from .furniture import board_id, build_korpus


def _korpus() -> Document:
    """Cabinet with the back panel 10 mm in front of the rear edge, in grooves."""
    doc = build_korpus()
    back = board_id(doc, "Rückwand")
    doc.set_expression(back, "y", "T - 20")
    fit.seat_in_groove(doc, Evaluator().evaluate(doc), back, fit.GrooveSettings(8, 0.5, 10))
    return doc


def _divider(doc: Document) -> str:
    """A divider in the middle, as deep as the cabinet (into the back panel)."""
    board = doc.add_board("Trennwand", orientation=Orientation.ZY)
    for prop, text in {
        "length": "Deckel.z - Boden.z2",
        "width": "T",
        "thickness": "t",
        "x": "391",
        "z": "Boden.z2",
    }.items():
        doc.set_expression(board.id, prop, text)
    doc.add_joint(JointKind.DOWEL, board_id(doc, "Boden"), board.id)
    doc.add_joint(JointKind.DOWEL, board_id(doc, "Deckel"), board.id)
    return board.id


def test_seat_marks_back_panel_and_distance() -> None:
    doc = _korpus()
    back = board_id(doc, "Rückwand")
    ev = Evaluator().evaluate(doc)
    assert doc.boards[back].back_panel
    assert backpanel.back_distance(doc, ev, back) == pytest.approx(10)
    backpanel.set_back_distance(doc, ev, back, 25)
    ev = Evaluator().evaluate(doc)
    assert backpanel.back_distance(doc, ev, back) == pytest.approx(25)
    assert ev.boards[back].y == pytest.approx(400 - 25 - 3)


def test_parts_stop_at_back_and_follow() -> None:
    doc = _korpus()
    back = board_id(doc, "Rückwand")
    shelf = board_id(doc, "Fachboden")
    doc.set_expression(shelf, "width", "T")  # reaches into the back panel
    divider = _divider(doc)
    ev = Evaluator().evaluate(doc)
    changed = backpanel.stop_at_back(doc, ev)
    assert set(changed) == {shelf, divider}
    assert doc.display_expression(shelf, "width") == "Rückwand.y - Fachboden.y"
    ev = Evaluator().evaluate(doc)
    assert ev.boards[shelf].width == pytest.approx(387)
    assert ev.boards[divider].width == pytest.approx(387)
    # the parts the panel sits in are untouched
    assert doc.display_expression(board_id(doc, "Boden"), "width") == "T"
    backpanel.set_back_distance(doc, ev, back, 30)
    ev = Evaluator().evaluate(doc)
    assert ev.boards[shelf].width == pytest.approx(367)


def test_short_parts_and_free_panel_left_alone() -> None:
    doc = build_korpus()  # back panel nailed on behind the carcass
    doc.boards[board_id(doc, "Rückwand")].back_panel = True
    assert backpanel.stop_at_back(doc, Evaluator().evaluate(doc)) == []
    doc = _korpus()  # shelf is T - 20 deep: ends before the panel
    assert backpanel.stop_at_back(doc, Evaluator().evaluate(doc)) == []


def test_pass_through_splits_and_join_merges() -> None:
    doc = _korpus()
    divider = _divider(doc)
    backpanel.stop_at_back(doc, Evaluator().evaluate(doc))
    (half,) = backpanel.pass_through(doc, Evaluator().evaluate(doc), divider)
    ev = Evaluator().evaluate(doc)
    back = board_id(doc, "Rückwand")
    assert ev.boards[divider].width == pytest.approx(400)
    for panel in (back, half):
        assert doc.boards[panel].back_panel
        partners = {j.main_id for j in doc.joints_of(panel) if j.kind == JointKind.GROOVE}
        assert divider in partners and len(partners) == 4
        assert backpanel.back_distance(doc, ev, panel) == pytest.approx(10)
    # face to face with the divider (the grooves are machined by the joints)
    assert ev.boards[back].x + ev.boards[back].length == pytest.approx(391)
    assert ev.boards[half].x == pytest.approx(409)

    removed = backpanel.join_back(doc, ev, divider)
    assert removed == [half] and half not in doc.boards
    ev = Evaluator().evaluate(doc)
    assert ev.boards[back].length == pytest.approx(800 - 2 * 18)
    assert {j.main_id for j in doc.joints_of(back)} == {
        board_id(doc, n) for n in ("Seite_links", "Seite_rechts", "Boden", "Deckel")
    }
    assert doc.display_expression(divider, "width") == "Rückwand.y - Trennwand.y"
    assert ev.boards[divider].width == pytest.approx(387)


def test_pass_through_needs_a_panel_behind() -> None:
    doc = build_korpus()
    divider = _divider(doc)
    with pytest.raises(ValueError):
        backpanel.pass_through(doc, Evaluator().evaluate(doc), divider)


def test_flags_saved(tmp_path: Path) -> None:
    doc = _korpus()
    divider = _divider(doc)
    doc.boards[divider].through_back = True
    path = tmp_path / "a.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert loaded.boards[board_id(loaded, "Rückwand")].back_panel
    assert loaded.boards[divider].through_back
    assert not loaded.boards[board_id(loaded, "Boden")].back_panel


def test_controller_steps_are_undoable(qtbot: QtBot, tmp_path: Path) -> None:
    c = Controller(recovery_dir=tmp_path / "recovery")
    c._replace_document(_korpus(), None)
    back = board_id(c.doc, "Rückwand")
    divider = c.execute("Trennwand", _divider)
    c.set_back_panel(back, True)
    assert c.evaluation.boards[divider].width == pytest.approx(387)
    count = len(c.doc.boards)
    assert c.set_through_back(divider, True)
    assert c.doc.boards[divider].through_back
    assert len(c.doc.boards) == count + 1
    assert c.set_through_back(divider, False)
    assert len(c.doc.boards) == count
    c.undo_stack.undo()
    c.undo_stack.undo()
    assert len(c.doc.boards) == count
    assert c.set_back_distance(back, 20)
    assert c.evaluation.boards[divider].width == pytest.approx(377)
    assert not c.set_through_back(board_id(c.doc, "Seite_links"), True)
    c.undo_stack.setClean()


def test_properties_panel_back_settings(qtbot: QtBot, tmp_path: Path) -> None:
    from simpledesign.ui.properties_panel import PropertiesPanel

    c = Controller(recovery_dir=tmp_path / "recovery")
    c._replace_document(_korpus(), None)
    panel = PropertiesPanel(c)
    qtbot.addWidget(panel)
    panel.show()
    back = board_id(c.doc, "Rückwand")
    c.set_selection([back])
    assert panel.back_panel_check.isChecked()
    assert panel.back_distance_spin.isVisible()
    assert panel.back_distance_spin.value() == pytest.approx(10)
    assert not panel.through_back_check.isVisible()
    panel.back_distance_spin.setValue(15)
    panel.back_distance_spin.editingFinished.emit()
    assert backpanel.back_distance(c.doc, c.evaluation, back) == pytest.approx(15)

    divider = c.execute("Trennwand", _divider)
    c.set_selection([divider])
    assert panel.through_back_check.isVisible() and not panel.back_distance_spin.isVisible()
    panel.through_back_check.click()
    assert c.doc.boards[divider].through_back
    assert sum(b.back_panel for b in c.doc.boards.values()) == 2
    # a side cannot go through: message, box unchecked again
    c.set_selection([board_id(c.doc, "Seite_links")])
    panel.through_back_check.click()
    assert not panel.through_back_check.isChecked()
    assert panel.back_message.isVisible()
    c.set_selection([back])
    assert not panel.back_message.isVisible()
    c.undo_stack.setClean()
