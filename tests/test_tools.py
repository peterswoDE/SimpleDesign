"""Click tools of the 3D view: insert boards, seat in a groove, measure."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from simpledesign.core.model import fit
from simpledesign.core.model.joint import JointKind
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.tools import InsertTool, MeasureTool

from .furniture import board_id, build_korpus


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    win.scene.snapping_enabled = False
    doc = build_korpus()
    doc.delete([board_id(doc, "Fachboden")], {})
    win.controller._replace_document(doc, None)
    win.viewport.show_view("front")
    win.viewport.render()
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def _screen(window: MainWindow, point: tuple[float, float, float]) -> tuple[int, int]:
    x, y = window.scene.to_display(point)
    return round(x), round(y)


def test_divider_tool_fits_into_compartment(window: MainWindow) -> None:
    c = window.controller
    window.action_insert_divider.trigger()
    tool = window.scene.tool
    assert isinstance(tool, InsertTool)
    # pointing at the back panel in the middle of the cabinet
    x, y = _screen(window, (402, 400, 900))
    tool.hover(x, y)
    assert tool.plan is not None and tool.plan.centred
    assert tool.preview.GetVisibility()
    count = c.undo_stack.count()
    assert tool.press(x, y, False) is True
    assert c.undo_stack.count() == count + 1
    (new,) = c.selection
    state = c.evaluation.boards[new]
    assert state.x == pytest.approx(391)
    assert state.z == pytest.approx(18)
    assert {c.doc.boards[j.main_id].name for j in c.doc.joints_of(new)} == {"Boden", "Deckel"}
    assert c.doc.display_expression(new, "thickness") == "t"


def test_shelf_tool_typed_gap(window: MainWindow) -> None:
    c = window.controller
    window.action_insert_shelf.trigger()
    tool = window.scene.tool
    assert isinstance(tool, InsertTool)
    x, y = _screen(window, (200, 400, 700))
    tool.hover(x, y)
    for char in "300":
        assert tool.type_key(0, char)
    assert tool.plan is not None and tool.plan.gap == pytest.approx(300)
    assert tool.type_key(Qt.Key.Key_Return, "\r")
    (new,) = c.selection
    assert c.evaluation.boards[new].z == pytest.approx(318)


def test_tool_ends_with_escape_and_unchecks(window: MainWindow) -> None:
    window.action_insert_shelf.trigger()
    assert window.scene.tool is not None
    assert window.viewport.interaction_style.pointer_handler is window.scene.tool
    window.scene.tool.cancel()
    assert window.scene.tool is None
    assert window.viewport.interaction_style.pointer_handler is window.scene
    assert not window.action_insert_shelf.isChecked()
    # switching tools replaces the active one
    window.action_insert_divider.trigger()
    window.action_measure.trigger()
    assert isinstance(window.scene.tool, MeasureTool)
    assert not window.action_insert_divider.isChecked()
    window.action_measure.trigger()  # unchecking ends it
    assert window.scene.tool is None


def test_seat_back_in_groove_from_menu(window: MainWindow) -> None:
    c = window.controller
    back = board_id(c.doc, "Rückwand")
    c.set_expression(back, "y", "T - 20")
    c.set_selection([back])
    dialog = window.seat_in_groove()
    assert dialog is not None
    dialog.depth_spin.setValue(6)
    dialog.accept()
    joints = c.doc.joints_of(back)
    assert len(joints) == 4 and all(j.kind == JointKind.GROOVE for j in joints)
    assert c.groove.depth == 6
    assert window.preferences.groove_depth == 6
    # a free panel: message, nothing changes
    window.action_add_board.trigger()
    count = c.undo_stack.count()
    dialog = window.seat_in_groove()
    assert dialog is not None
    dialog.accept()
    assert c.undo_stack.count() == count
    assert "umgeben" in window.statusBar().currentMessage()


def test_measure_snaps_to_corners(window: MainWindow) -> None:
    window.action_measure.trigger()
    tool = window.scene.tool
    assert isinstance(tool, MeasureTool)
    # inner bottom corners of the compartment (front of the back panel)
    a = _screen(window, (18, 400, 18))
    b = _screen(window, (782, 400, 18))
    assert tool.press(*a, False)
    tool.hover(*b)
    assert tool.press(*b, False)
    assert tool.result is not None
    first, second = tool.result
    assert abs(second[0] - first[0]) == pytest.approx(764, abs=0.5)
    assert second[2] == pytest.approx(first[2], abs=0.5)


def test_groove_settings_from_preferences(window: MainWindow) -> None:
    prefs = window.preferences
    prefs.groove_distance = 15
    window.apply_preferences(prefs)
    assert window.controller.groove == fit.GrooveSettings(prefs.groove_depth, prefs.groove_play, 15)
