from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.close()


def test_window_has_german_title_and_docks(window: MainWindow) -> None:
    assert window.windowTitle() == "SimpleDesign"
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
