"""Mouse controls in the 3D view: orbit, gizmo, dragging parts, context menu."""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.model.part import Axis
from simpledesign.ui.camera import turntable
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.scene import UNIT, segment_distance


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    win.scene.snapping_enabled = False
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def _distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.dist(a, b)


# ---------------------------------------------------------------- camera maths
def test_turntable_keeps_distance_to_pivot_and_z_up() -> None:
    pivot = (100.0, 50.0, 20.0)
    position, focal, up = (1000.0, -1000.0, 800.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    new_position, new_focal, new_up = turntable(position, focal, up, pivot, 30.0, 10.0)
    assert _distance(new_position, pivot) == pytest.approx(_distance(position, pivot))
    assert _distance(new_focal, pivot) == pytest.approx(_distance(focal, pivot))
    assert new_up[2] > 0.5  # Z still points up on screen


def test_turntable_azimuth_turns_about_z() -> None:
    position, focal, up = (0.0, -1000.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    new_position, _focal, _up = turntable(position, focal, up, focal, 90.0, 0.0)
    assert new_position == pytest.approx((1000.0, 0.0, 0.0), abs=1e-6)


def test_turntable_elevation_is_clamped() -> None:
    position, focal, up = (0.0, -1000.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    new_position, _focal, new_up = turntable(position, focal, up, focal, 0.0, 500.0)
    elevation = math.degrees(math.asin(new_position[2] / 1000.0))
    assert elevation == pytest.approx(89.0, abs=1e-6)
    assert new_up[1] > 0.0  # never flipped over the pole


def test_segment_distance() -> None:
    assert segment_distance((5, 3), (0, 0), (10, 0)) == pytest.approx(3)
    assert segment_distance((-4, 3), (0, 0), (10, 0)) == pytest.approx(5)


# ---------------------------------------------------------------- viewport
def test_orbit_and_pan_move_the_camera(window: MainWindow) -> None:
    style = window.viewport.interaction_style
    camera = window.viewport.renderer.GetActiveCamera()
    style.set_pivot(camera.GetFocalPoint())
    distance = camera.GetDistance()
    before = camera.GetPosition()
    style.orbit(60, 20)
    assert camera.GetPosition() != pytest.approx(before)
    assert camera.GetDistance() == pytest.approx(distance)

    focal = camera.GetFocalPoint()
    style.pan(40, 0)
    assert camera.GetFocalPoint() != pytest.approx(focal)
    assert camera.GetDistance() == pytest.approx(distance)


def _center(window: MainWindow) -> tuple[int, int]:
    width, height = window.viewport.renderer.GetSize()
    return width // 2, height // 2


def test_press_on_empty_space_is_left_to_the_camera(window: MainWindow) -> None:
    assert window.scene.press(5, 5, False) is False
    window.scene.release(5, 5, False)


def test_drag_part_moves_it_in_one_undo_step(window: MainWindow) -> None:
    controller = window.controller
    window.action_add_board.trigger()
    (board_id,) = controller.doc.boards
    controller.set_selection([])
    window.viewport.show_view("top")
    x, y = _center(window)
    scene = window.scene
    count = controller.undo_stack.count()

    assert scene.press(x, y, False) is True
    for step in range(1, 11):
        scene.move(x + 8 * step, y)
    scene.release(x + 80, y, False)

    state = controller.evaluation.boards[board_id]
    assert state.x > 10
    assert state.y == pytest.approx(0, abs=1.0)
    assert state.z == pytest.approx(0)
    assert controller.undo_stack.count() == count + 1
    assert controller.selection == [board_id]
    controller.undo_stack.undo()
    assert controller.evaluation.boards[board_id].x == pytest.approx(0)


def test_escape_cancels_a_drag(window: MainWindow) -> None:
    controller = window.controller
    window.action_add_board.trigger()
    (board_id,) = controller.doc.boards
    controller.set_selection([])
    window.viewport.show_view("top")
    x, y = _center(window)
    count = controller.undo_stack.count()
    window.scene.press(x, y, False)
    window.scene.move(x + 60, y)
    window.viewport.interaction_style.cancel()
    window.scene.release(x + 60, y, False)
    assert controller.undo_stack.count() == count
    assert controller.evaluation.boards[board_id].x == pytest.approx(0)


def _arrow_points(window: MainWindow, axis: Axis) -> tuple[tuple[float, float], ...]:
    scene = window.scene
    origin = scene.gizmo_origin()
    assert origin is not None
    scale = 90 * scene.world_per_pixel(origin)
    unit = UNIT[axis]
    tip = tuple(origin[i] + unit[i] * scale for i in range(3))
    start = scene.to_display(origin)
    end = scene.to_display((tip[0], tip[1], tip[2]))
    return start, end


def test_gizmo_arrow_is_picked_in_screen_space(window: MainWindow) -> None:
    window.action_add_board.trigger()
    start, end = _arrow_points(window, Axis.X)
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    assert window.scene.arrow_at(round(mid[0]), round(mid[1]) + 6) == Axis.X
    beyond = (2 * end[0] - start[0], 2 * end[1] - start[1])  # far past the tip
    assert window.scene.arrow_at(round(beyond[0]), round(beyond[1])) is None


def test_gizmo_moves_a_group_with_its_boards(window: MainWindow) -> None:
    controller = window.controller
    first = controller.add_board()
    second = controller.add_board()
    group = controller.add_group(None, [first, second])
    controller.set_selection([group])
    window.viewport.fit_view()
    start, end = _arrow_points(window, Axis.Z)
    press = (round((start[0] + end[0]) / 2), round((start[1] + end[1]) / 2))
    before = {b: controller.evaluation.boards[b].z for b in (first, second)}
    count = controller.undo_stack.count()

    scene = window.scene
    assert scene.press(*press, False) is True
    scene.move(press[0], press[1] + 50)
    scene.release(press[0], press[1] + 50, False)

    for board_id in (first, second):
        assert controller.evaluation.boards[board_id].z > before[board_id] + 10
    assert controller.undo_stack.count() == count + 1


def test_right_click_selects_part_and_requests_menu(window: MainWindow) -> None:
    controller = window.controller
    window.action_add_board.trigger()
    (board_id,) = controller.doc.boards
    controller.set_selection([])
    window.viewport.fit_view()
    requests: list[tuple[int, int]] = []
    window.scene.context_menu_requested.connect(lambda x, y: requests.append((x, y)))
    window.scene.context_menu(*_center(window))
    assert controller.selection == [board_id]
    assert len(requests) == 1


def test_view_menu_offers_part_actions(window: MainWindow) -> None:
    window.action_add_board.trigger()
    menu = window.show_view_menu(10, 10)
    assert window.tree_panel.action_delete in menu.actions()
    menu.close()
    window.controller.set_selection([])
    menu = window.show_view_menu(10, 10)
    assert window.tree_panel.action_delete not in menu.actions()
    assert window.action_fit in menu.actions()
    menu.close()
