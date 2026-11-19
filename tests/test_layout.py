"""Window layout: the cut list and cut plan take the room of the 3D view."""

from __future__ import annotations

from pytestqt.qtbot import QtBot


def test_lists_take_the_room_of_the_3d_view(qtbot: QtBot) -> None:
    from simpledesign.ui.main_window import MainWindow

    win = MainWindow()
    try:
        win.resize(1400, 900)
        win.show()
        qtbot.waitExposed(win)
        win.parameters_dock.raise_()
        qtbot.wait(50)
        assert not win.lists_maximized()
        normal_view = win.viewport.height()
        normal_dock = win.parameters_dock.height()
        assert normal_view > 300
        win.cutplan_dock.raise_()
        qtbot.waitUntil(lambda: win.cutplan_dock.height() > normal_dock + normal_view - 30)
        assert win.lists_maximized()
        assert not win.viewport.geometry().intersects(win.rect())  # no strip of 3D left
        assert not win.properties_dock.isVisible()  # side panels make room too
        win.cutlist_dock.raise_()
        qtbot.waitUntil(lambda: win.cutlist_dock.height() > normal_dock + normal_view - 30)
        # back to the parameters: the 3D view gets its room back
        win.parameters_dock.raise_()
        qtbot.waitUntil(lambda: win.viewport.height() > normal_view - 10)
        assert not win.lists_maximized() and win.viewport.isVisible()
        assert win.properties_dock.isVisible()
        assert win.viewport.minimumSize().toTuple() == (320, 200)  # never squeezed away
        assert abs(win.parameters_dock.height() - normal_dock) < 10
        # a floating list leaves the 3D view in place
        win.cutplan_dock.setFloating(True)
        win.cutplan_dock.raise_()
        qtbot.wait(50)
        assert not win.lists_maximized()
        assert win.viewport.height() > normal_view - 10
    finally:
        win.close()
        win.deleteLater()
