"""Phase 7: preferences and shortcut editor."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QKeySequence
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.settings_dialog import Preferences


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[MainWindow]:
    controller = Controller(recovery_dir=tmp_path / "recovery", library_path=tmp_path / "l.json")
    win = MainWindow(controller)
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def test_preferences_round_trip() -> None:
    prefs = Preferences(snapping=False, snap_tolerance_px=12, board_thickness=19.0)
    prefs.save()
    assert Preferences.load() == prefs


def test_settings_dialog_applies(window: MainWindow) -> None:
    dialog = window.show_settings()
    dialog.snap_spin.setValue(15)
    dialog.autosave_spin.setValue(5)
    dialog.size_spins[2].setValue(22)
    dialog.accept()
    assert window.scene.snap_tolerance_px == 15
    assert window.controller.autosave_timer.interval() == 5 * 60_000
    board = window.controller.add_board()
    assert window.controller.doc.boards[board].expressions["thickness"] == "22"
    assert Preferences.load().board_thickness == 22


def test_shortcuts_dialog(window: MainWindow) -> None:
    dialog = window.show_shortcuts()
    assert "action_add_board" in dialog.editors
    dialog.editors["action_add_board"].setKeySequence(QKeySequence("Ctrl+1"))
    assert dialog.conflicts() == ["Ctrl+1"] and not dialog.ok_button.isEnabled()
    dialog.editors["action_add_board"].setKeySequence(QKeySequence("Shift+B"))
    assert dialog.ok_button.isEnabled()
    dialog.accept()
    assert window.action_add_board.shortcut().toString() == "Shift+B"
    dialog = window.show_shortcuts()
    dialog.restore_defaults()
    dialog.accept()
    assert window.action_add_board.shortcut().toString() == "B"
