"""Resize handles and rotate rings in the 3D view."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.model.part import Axis
from simpledesign.ui.main_window import MainWindow


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


def _board(window: MainWindow) -> str:
    window.action_add_board.trigger()  # 600 × 300 × 18, selected
    (board_id,) = window.controller.doc.boards
    window.viewport.show_view("iso")
    window.viewport.fit_view()
    window.viewport.render()
    return board_id


def _screen(window: MainWindow, point: tuple[float, float, float]) -> tuple[int, int]:
    x, y = window.scene.to_display(point)
    return round(x), round(y)


def _drag_along(
    window: MainWindow, start: tuple[float, float, float], direction: tuple[float, float, float]
) -> None:
    scene = window.scene
    end = tuple(start[i] + direction[i] for i in range(3))
    sx, sy = _screen(window, start)
    ex, ey = _screen(window, (end[0], end[1], end[2]))
    assert scene.press(sx, sy, False) is True
    for step in range(1, 11):
        scene.move(sx + (ex - sx) * step // 10, sy + (ey - sy) * step // 10)
    scene.release(ex, ey, False)


def test_handles_on_faces_of_selected_board(window: MainWindow) -> None:
    _board(window)
    points = window.scene._handle_points
    assert points[Axis.X, 1] == pytest.approx((600, 150, 9))
    assert points[Axis.Y, -1] == pytest.approx((300, 0, 9))
    window.controller.set_selection([])
    assert window.scene._handle_points == {}


def test_drag_end_handle_extends_length(window: MainWindow) -> None:
    board_id = _board(window)
    c = window.controller
    count = c.undo_stack.count()
    sx, sy = _screen(window, (600, 150, 9))
    assert window.scene.handle_at(sx, sy) == (Axis.X, 1)
    _drag_along(window, (600, 150, 9), (200, 0, 0))
    state = c.evaluation.boards[board_id]
    assert state.length == pytest.approx(800, abs=15)
    assert state.x == pytest.approx(0)
    assert c.doc.display_expression(board_id, "length") == f"{state.length:g}"  # plain number
    assert c.undo_stack.count() == count + 1


def test_drag_start_handle_keeps_far_face(window: MainWindow) -> None:
    board_id = _board(window)
    c = window.controller
    _drag_along(window, (300, 0, 9), (0, 100, 0))
    state = c.evaluation.boards[board_id]
    assert state.y == pytest.approx(100, abs=10)
    assert state.y + state.width == pytest.approx(300)


def test_resize_cannot_collapse(window: MainWindow) -> None:
    board_id = _board(window)
    _drag_along(window, (300, 300, 9), (0, -800, 0))
    assert window.controller.evaluation.boards[board_id].width == pytest.approx(1)


def _drag_ring(window: MainWindow, axis: Axis, start_deg: float, end_deg: float) -> None:
    scene = window.scene
    sx, sy = _screen(window, scene.ring_point(axis, start_deg))
    assert scene.ring_at(sx, sy) == axis
    assert scene.press(sx, sy, False) is True
    for step in range(1, 21):
        deg = start_deg + (end_deg - start_deg) * step / 20
        scene.move(*_screen(window, scene.ring_point(axis, deg)))
    scene.release(*_screen(window, scene.ring_point(axis, end_deg)), False)


def test_rotate_ring_turns_about_centre(window: MainWindow) -> None:
    board_id = _board(window)
    c = window.controller
    before = c.evaluation.boards[board_id]
    window.action_rotate_mode.trigger()
    assert window.scene.rotate_mode
    assert window.scene._handle_points == {}
    assert window.scene.arrow_at(*_screen(window, (300, 150, 9))) is None
    count = c.undo_stack.count()
    # Z rotation: ring in the XY plane; a 90° turn on the ring (counter-clockwise
    # seen from above) turns the board by +90°.
    _drag_ring(window, Axis.Z, 10, 100)
    after = c.evaluation.boards[board_id]
    assert after.angle == pytest.approx(90)
    assert c.undo_stack.count() == count + 1
    lo, hi = window.scene.bounds({board_id}) or ((0, 0, 0), (0, 0, 0))
    centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
    assert centre == pytest.approx([300, 150, 9], abs=0.5)
    assert hi[0] - lo[0] == pytest.approx(300, abs=0.5)  # turned: width along X
    # an already rotated board offers only its own axis
    assert window.scene.ring_axes() == [Axis.Z]
    assert before.angle == 0
