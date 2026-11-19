"""Comfort tools: array copy, isolate, X-ray."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def _visible_boards(window: MainWindow) -> set[str]:
    return set(window.scene._board_actors)


def test_array_copy_in_one_step(window: MainWindow) -> None:
    c = window.controller
    window.action_add_board.trigger()  # 600 × 300 × 18
    (original,) = c.selection
    count = c.undo_stack.count()
    dialog = window.array_copy()
    assert dialog is not None
    assert dialog.spacing_spin.value() == pytest.approx(600)  # side by side along X
    dialog.axis_combo.setCurrentIndex(2)
    assert dialog.spacing_spin.value() == pytest.approx(18)
    dialog.spacing_spin.setValue(100)
    dialog.count_spin.setValue(4)
    dialog.accept()
    assert c.undo_stack.count() == count + 1
    copies = c.selection
    assert len(copies) == 4 and original not in copies
    heights = sorted(c.evaluation.boards[b].z for b in copies)
    assert heights == pytest.approx([100, 200, 300, 400])
    c.undo_stack.undo()
    assert list(c.doc.boards) == [original]


def test_isolate_shows_only_selection(window: MainWindow) -> None:
    c = window.controller
    window.action_add_board.trigger()
    window.action_add_board.trigger()
    first, second = c.doc.boards
    c.set_selection([first])
    window.action_isolate.trigger()
    assert window.scene.isolated
    assert _visible_boards(window) == {first}
    window.action_add_board.trigger()  # new parts stay visible
    assert len(_visible_boards(window)) == 2
    window.action_isolate.trigger()
    assert not window.scene.isolated
    assert _visible_boards(window) == set(c.doc.boards)
    assert second in _visible_boards(window)


def test_isolate_without_selection_does_nothing(window: MainWindow) -> None:
    window.action_isolate.trigger()
    assert not window.scene.isolated
    assert not window.action_isolate.isChecked()


def test_isolation_ends_with_new_file(window: MainWindow) -> None:
    window.action_add_board.trigger()
    window.action_isolate.trigger()
    window.controller.new_document()
    assert not window.scene.isolated
    assert not window.action_isolate.isChecked()


def test_xray_makes_others_transparent(window: MainWindow) -> None:
    c = window.controller
    window.action_add_board.trigger()
    window.action_add_board.trigger()
    first, second = c.doc.boards
    c.set_selection([first])
    window.action_xray.trigger()

    def opacity(board_id: str) -> float:
        face, _edge = window.scene._board_actors[board_id][0]
        return float(face.GetProperty().GetOpacity())

    assert opacity(first) == 1.0
    assert opacity(second) < 1.0
    window.action_xray.trigger()
    assert opacity(second) == 1.0
