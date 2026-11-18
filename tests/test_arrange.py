"""Align, distribute and measure parts."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.model import arrange
from simpledesign.core.model.arrange import AlignMode
from simpledesign.core.model.part import Axis
from simpledesign.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def _board(win: MainWindow, x: float, y: float = 0, z: float = 0) -> str:
    controller = win.controller
    board_id = controller.add_board()
    controller.place_board(
        board_id, {Axis.X: (str(x), x), Axis.Y: (str(y), y), Axis.Z: (str(z), z)}
    )
    return board_id


def _box(win: MainWindow, obj_id: str) -> arrange.Box:
    box = arrange.bounds(win.controller.doc, [obj_id])
    assert box is not None
    return box


def test_align_to_first_in_one_undo_step(window: MainWindow) -> None:
    c = window.controller
    a, b = _board(window, 0, 0, 0), _board(window, 1000, 300, 200)
    c.set_selection([a, b])
    steps = c.undo_stack.count()
    window._align(Axis.Z, AlignMode.MIN)
    assert c.undo_stack.count() == steps + 1
    assert _box(window, b)[0][2] == pytest.approx(_box(window, a)[0][2])
    assert _box(window, b)[0][1] == pytest.approx(300)  # other axes untouched
    c.undo_stack.undo()
    assert _box(window, b)[0][2] == pytest.approx(200)


def test_align_center_and_max(window: MainWindow) -> None:
    c = window.controller
    a, b = _board(window, 0), _board(window, 0, 500, 0)
    c.align([a, b], Axis.X, AlignMode.MAX)
    assert _box(window, b)[1][0] == pytest.approx(_box(window, a)[1][0])
    c.align([a, b], Axis.Y, AlignMode.CENTER)
    ca, cb = _box(window, a), _box(window, b)
    assert (cb[0][1] + cb[1][1]) / 2 == pytest.approx((ca[0][1] + ca[1][1]) / 2)


def test_distribute_equal_gaps(window: MainWindow) -> None:
    c = window.controller
    ids = [_board(window, 0, 0, z) for z in (0, 100, 1000)]
    c.distribute(ids, Axis.Z)
    boxes = [_box(window, i) for i in ids]
    first_gap = boxes[1][0][2] - boxes[0][1][2]
    second_gap = boxes[2][0][2] - boxes[1][1][2]
    assert first_gap == pytest.approx(second_gap)
    assert boxes[0][0][2] == pytest.approx(0)  # outer parts stay
    assert boxes[2][0][2] == pytest.approx(1000)


def test_arrange_actions_and_measure_follow_selection(window: MainWindow) -> None:
    c = window.controller
    a, b = _board(window, 0), _board(window, 2000)
    c.set_selection([a])
    assert not any(action.isEnabled() for action, _ in window.align_actions)
    assert window.selection_info.text().startswith("Auswahl:")
    c.set_selection([a, b])
    enabled = [action.isEnabled() for action, minimum in window.align_actions if minimum == 2]
    assert all(enabled)
    assert "Abstand: X" in window.selection_info.text()
    c.set_selection([])
    assert window.selection_info.text() == ""
    menu = None
    c.set_selection([a, b])
    menu = window.show_view_menu(10, 10)
    titles = [action.text() for action in menu.actions()]
    assert "Anordnen" in titles
    menu.close()
