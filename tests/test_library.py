"""Phase 8: furniture library with previews, drag into the 3D view."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from pytestqt.qtbot import QtBot

from simpledesign.core.templates.builtin import builtin_templates
from simpledesign.ui.library_panel import (
    KEY_ROLE,
    MIME_TYPE,
    preview_faces,
    render_preview,
    template_key,
)
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


def test_library_has_many_kinds_of_furniture() -> None:
    names = {t.name for t in builtin_templates()}
    for name in ("Kommode", "Nachttisch", "Hängeschrank", "Sideboard", "Schreibtisch", "Bett"):
        assert name in names


@pytest.mark.parametrize("template", builtin_templates(), ids=lambda t: t.name)
def test_previews_draw_something(qtbot: QtBot, template: object) -> None:
    from simpledesign.core.templates.template import Template

    assert isinstance(template, Template)
    faces = preview_faces(template)
    assert faces and [f[0] for f in faces] == sorted(f[0] for f in faces)
    image = render_preview(template).toImage()
    painted = sum(
        1
        for x in range(0, image.width(), 4)
        for y in range(0, image.height(), 4)
        if image.pixelColor(x, y).alpha() > 0
    )
    assert painted > 50


def test_panel_lists_templates_with_drag_data(window: MainWindow) -> None:
    panel = window.library_panel
    assert panel.list.count() == len(builtin_templates())
    item = panel.list.item(0)
    data = panel.list.mimeData([item])
    assert bytes(data.data(MIME_TYPE).data()).decode() == item.data(KEY_ROLE)


def test_double_click_inserts(window: MainWindow) -> None:
    ctrl = window.controller
    key = template_key(builtin_templates()[0])
    group = window.insert_library_template(key)
    assert group is not None and ctrl.selection == [group]


def test_drop_places_furniture_on_the_floor_under_the_cursor(window: MainWindow) -> None:
    ctrl = window.controller
    window.viewport.show_view("top")
    key = template_key(next(t for t in builtin_templates() if t.name == "Kommode"))
    width, height = window.viewport.width(), window.viewport.height()

    data = QMimeData()
    data.setData(MIME_TYPE, key.encode())
    for x in (width // 2, width // 2 + 100):
        event = QDropEvent(
            QPointF(x, height // 2),
            Qt.DropAction.CopyAction,
            data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        window.viewport.dropEvent(event)
    groups = [g for g in ctrl.doc.groups.values() if g.name.startswith("Kommode")]
    assert len(groups) == 2
    lefts = sorted(
        ctrl.evaluation.boards[b.id].x
        for b in ctrl.doc.boards.values()
        if b.name.startswith("Seite_links")
    )
    assert lefts[1] - lefts[0] > 50  # dropped further right → placed further right
    assert all(ctrl.evaluation.boards[b].z >= -1e-6 for b in ctrl.doc.boards)
