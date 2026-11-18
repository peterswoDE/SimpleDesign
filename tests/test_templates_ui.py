"""Phase 4: templates in the UI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.templates.builtin import builtin_templates
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.templates_dialog import (
    InsertTemplateDialog,
    SaveTemplateDialog,
    insert_template,
)
from tests.furniture import board_id, build_korpus


@pytest.fixture
def controller(tmp_path: Path) -> Controller:
    ctrl = Controller(recovery_dir=tmp_path / "recovery", library_path=tmp_path / "lib.json")
    path = tmp_path / "korpus.sdz"
    sdz.save(build_korpus(), path)
    ctrl.open(path)
    return ctrl


@pytest.fixture
def window(qtbot: QtBot, controller: Controller) -> Iterator[MainWindow]:
    win = MainWindow(controller)
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def test_insert_dialog_lists_and_inserts(window: MainWindow, qtbot: QtBot) -> None:
    ctrl = window.controller
    dialog = InsertTemplateDialog(ctrl.templates, window)
    qtbot.addWidget(dialog)
    assert dialog.list.count() == len(builtin_templates())
    assert not dialog.remove_button.isEnabled()
    dialog.list.setCurrentRow(0)  # Regal
    assert dialog.parameters() == {}
    dialog._fields["B"].setText("600")
    assert dialog.parameters() == {"B": "600"}
    right_before = max(s.origin[0] + s.size[0] for s in ctrl.evaluation.boards.values())
    assert insert_template(dialog, ctrl.insert_template)
    group = ctrl.selection[0]
    assert ctrl.doc.groups[group].name == "Regal"
    left = ctrl.evaluation.boards[board_id(ctrl.doc, "Seite_links_2")]
    assert left.origin[0] == pytest.approx(right_before + 200)
    right = ctrl.evaluation.boards[board_id(ctrl.doc, "Seite_rechts_2")]
    assert right.origin[0] + right.size[0] - left.origin[0] == pytest.approx(600)
    ctrl.undo_stack.undo()
    assert group not in ctrl.doc.groups


def test_insert_with_bad_formula_warns(
    window: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda _p, _t, text: warnings.append(text))
    )
    ctrl = window.controller
    dialog = InsertTemplateDialog(ctrl.templates, window)
    qtbot.addWidget(dialog)
    dialog._fields["H"].setText("1800 +")
    groups = len(ctrl.doc.groups)
    assert not insert_template(dialog, ctrl.insert_template)
    assert warnings and len(ctrl.doc.groups) == groups


def test_save_group_as_template(
    window: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctrl = window.controller
    ctrl.add_group(None, list(ctrl.doc.boards))
    group = ctrl.selection[0]
    ctrl.set_selection([group])
    assert window.tree_panel.selected_group() == group

    dialog = window.tree_panel.save_group_as_template()
    assert isinstance(dialog, SaveTemplateDialog)
    dialog.name.setText("Mein Regal")
    dialog.description.setPlainText("selbst gebaut")
    library_count = window.library_panel.list.count()
    dialog.accept()
    assert window.library_panel.list.count() == library_count + 1
    path = ctrl.templates.path_for("Mein Regal")
    assert path.exists()

    dialog = InsertTemplateDialog(ctrl.templates, window)
    qtbot.addWidget(dialog)
    dialog.list.setCurrentRow(dialog.list.count() - 1)
    template = dialog.template()
    assert template is not None and template.name == "Mein Regal"
    assert dialog.remove_button.isEnabled()
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *_a: QMessageBox.StandardButton.Yes)
    )
    dialog.remove_button.click()
    assert not path.exists()
    assert dialog.list.count() == len(builtin_templates())


def test_template_actions_in_menus(window: MainWindow) -> None:
    texts = [a.text() for a in window.menuBar().actions()]
    assert "&Einfügen" in texts
    assert window.action_insert_template.isEnabled()
    window.controller.set_selection([])
    assert window.tree_panel.selected_group() is None
    window.tree_panel.save_group_as_template()  # no group selected: nothing happens


def test_insert_dialog_is_not_modal_and_stays_open_on_errors(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *_a: None))
    ctrl = window.controller
    dialog = window.insert_template()
    assert dialog.isVisible()
    dialog._fields["H"].setText("1800 +")
    dialog.accept()
    assert dialog.isVisible()
    dialog._fields["H"].setText("1500")
    groups = len(ctrl.doc.groups)
    dialog.accept()
    assert len(ctrl.doc.groups) == groups + 1
