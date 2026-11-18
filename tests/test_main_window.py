from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    # Not qtbot.addWidget(): pytest-qt would close the window *before* this
    # fixture's teardown, i.e. with unsaved changes -> "save changes?" dialog.
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def test_window_has_german_title_and_docks(window: MainWindow) -> None:
    assert window.windowTitle() == "Unbenannt[*] - SimpleDesign"
    assert window.parts_dock.windowTitle() == "Bauteile"
    assert window.properties_dock.windowTitle() == "Eigenschaften"
    assert window.parameters_dock.windowTitle() == "Parameter"


def test_undo_redo_start_disabled(window: MainWindow) -> None:
    assert not window.action_undo.isEnabled()
    assert not window.action_redo.isEnabled()


def test_grid_and_axes_are_in_scene(window: MainWindow) -> None:
    actors = window.viewport.renderer.actors
    for name in ("grid", "axis_x", "axis_y", "axis_z"):
        assert name in actors


def test_grid_is_not_pickable(window: MainWindow) -> None:
    assert not window.viewport.renderer.actors["grid"].GetPickable()


@pytest.mark.parametrize(
    ("view", "axis", "sign"),
    [("front", 1, -1), ("right", 0, 1), ("top", 2, 1)],
)
def test_standard_views_place_camera_correctly(
    window: MainWindow, view: str, axis: int, sign: int
) -> None:
    window.view_actions[view].trigger()
    camera = window.viewport.renderer.GetActiveCamera()
    position, focal = camera.GetPosition(), camera.GetFocalPoint()
    assert (position[axis] - focal[axis]) * sign > 0


def test_mouse_wheel_zooms_towards_scene(window: MainWindow) -> None:
    camera = window.viewport.renderer.GetActiveCamera()
    before = camera.GetDistance()
    window.viewport.interaction_style.zoom_to_cursor(1.15)
    assert camera.GetDistance() == pytest.approx(before / 1.15)


def test_add_board_appears_everywhere(window: MainWindow) -> None:
    window.action_add_board.trigger()
    (new_id,) = window.controller.doc.boards
    assert window.controller.selection == [new_id]
    assert window.tree_panel.tree.topLevelItemCount() == 1
    assert window.properties_panel.stack.currentWidget() is window.properties_panel.board_page
    assert window.cutlist_panel.table.rowCount() == 1
    assert window.scene.gizmo_board() == new_id
    assert window.windowTitle().startswith("Unbenannt")
    assert window.isWindowModified()
    assert window.action_undo.isEnabled()


def test_properties_field_sets_formula(window: MainWindow) -> None:
    window.action_add_board.trigger()
    (new_id,) = window.controller.doc.boards
    field = window.properties_panel.fields["length"]
    field.edit.setText("2 * 400")
    field.edit.setModified(True)
    field.edit.editingFinished.emit()
    assert window.controller.evaluation.boards[new_id].length == 800
    assert field.value_label.text() == "= 800"


def test_properties_field_shows_input_error(window: MainWindow) -> None:
    window.action_add_board.trigger()
    field = window.properties_panel.fields["length"]
    field.edit.setText("Unbekannt + 1")
    field.edit.setModified(True)
    field.edit.editingFinished.emit()
    assert "Unbekannt" in field.edit.toolTip()
    assert window.controller.undo_stack.count() == 1


def test_click_selects_board(window: MainWindow) -> None:
    window.action_add_board.trigger()
    (new_id,) = window.controller.doc.boards
    window.controller.set_selection([])
    window.viewport.fit_view()
    width, height = window.viewport.renderer.GetSize()
    scene = window.scene
    scene.press(width // 2, height // 2, False)
    scene.release(width // 2, height // 2, False)
    assert window.controller.selection == [new_id]
