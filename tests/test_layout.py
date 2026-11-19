"""Window layout: no 3D view while the cut list or cut plan tab is shown."""

from __future__ import annotations

from pytestqt.qtbot import QtBot


def test_viewport_hidden_for_cut_list_and_plan(qtbot: QtBot) -> None:
    from simpledesign.ui.main_window import MainWindow

    win = MainWindow()
    try:
        win.show()
        qtbot.waitExposed(win)
        win.parameters_dock.raise_()
        qtbot.waitUntil(lambda: not win.viewport.isHidden())
        win.cutplan_dock.raise_()
        qtbot.waitUntil(lambda: win.viewport.isHidden())
        win.cutlist_dock.raise_()
        qtbot.wait(50)
        assert win.viewport.isHidden()
        win.parameters_dock.raise_()
        qtbot.waitUntil(lambda: not win.viewport.isHidden())
        # a floating list leaves the 3D view in place
        win.cutplan_dock.setFloating(True)
        win.cutplan_dock.raise_()
        qtbot.wait(50)
        assert not win.viewport.isHidden()
    finally:
        win.close()
        win.deleteLater()
