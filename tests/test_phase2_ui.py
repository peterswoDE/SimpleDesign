"""Material library, cutting plan and 3D export in the UI."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.cutlist.optimize import optimize
from simpledesign.core.io import sdz
from simpledesign.core.materials.library import DEFAULT_MATERIAL_ID, MaterialLibrary
from simpledesign.core.model.settings import CutSettings
from simpledesign.ui.cutplan_panel import CutplanPanel, write_pdf
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.materials_dialog import MaterialsDialog
from tests.furniture import board_id, build_korpus

MULTIPLEX = DEFAULT_MATERIAL_ID


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


def changed_library(controller: Controller, price: float) -> MaterialLibrary:
    library = controller.library.copy()
    library.materials[MULTIPLEX] = dataclasses.replace(library.materials[MULTIPLEX], price=price)
    return library


# ====================================================================== controller
def test_library_is_saved_and_reloaded(controller: Controller, tmp_path: Path) -> None:
    library = changed_library(controller, price=55.0)
    controller.set_library(library)
    other = Controller(recovery_dir=tmp_path / "other", library_path=controller.library_path)
    assert other.library == library


def test_broken_library_falls_back_to_builtins(tmp_path: Path) -> None:
    path = tmp_path / "lib.json"
    path.write_text("kaputt", encoding="utf-8")
    ctrl = Controller(recovery_dir=tmp_path, library_path=path)
    assert ctrl.library == MaterialLibrary()
    assert ctrl.library_error
    assert path.read_text(encoding="utf-8") == "kaputt"  # not overwritten


def test_document_copy_updates_only_on_request(controller: Controller) -> None:
    controller.set_library(changed_library(controller, price=55.0))
    assert controller.doc.materials[MULTIPLEX].price is None
    assert controller.outdated_materials() == [MULTIPLEX]
    controller.update_materials([MULTIPLEX])
    assert controller.doc.materials[MULTIPLEX].price == 55.0
    assert controller.outdated_materials() == []
    controller.undo_stack.undo()
    assert controller.doc.materials[MULTIPLEX].price is None


def test_new_board_uses_library_version(controller: Controller) -> None:
    controller.new_document()
    controller.set_library(changed_library(controller, price=12.0))
    board = controller.add_board()
    material_id = controller.doc.boards[board].material_id
    assert controller.doc.materials[material_id].price == 12.0


def test_ignore_grain_and_cut_settings_are_undoable(controller: Controller) -> None:
    side = board_id(controller.doc, "Seite_links")
    controller.set_ignore_grain([side], True)
    assert not controller.doc.has_grain(side)
    controller.set_cut_settings(CutSettings(3.0, 10.0))
    controller.set_cut_settings(CutSettings(2.5, 10.0))  # merged with the step before
    assert controller.doc.cut_settings.kerf == 2.5
    controller.undo_stack.undo()
    assert controller.doc.cut_settings.kerf == 4.0
    controller.undo_stack.undo()
    assert controller.doc.has_grain(side)


# ====================================================================== dialog
def test_materials_dialog_edits_a_copy(qtbot: QtBot) -> None:
    library = MaterialLibrary()
    dialog = MaterialsDialog(library)
    qtbot.addWidget(dialog)
    count = dialog.list.count()

    dialog.add_material()
    assert dialog.list.count() == count + 1
    dialog.name_edit.setText("Kiefer 27")
    dialog.name_edit.textEdited.emit("Kiefer 27")
    dialog.thickness_spin.setValue(27)
    dialog.price_spin.setValue(39.9)
    new_id = dialog.current_id()
    assert new_id is not None

    dialog.duplicate_material()
    copy_id = dialog.current_id()
    dialog.delete_material()
    assert copy_id not in dialog.materials

    result = dialog.library()
    material = result.materials[new_id]
    assert (material.name, material.thickness, material.price) == ("Kiefer 27", 27, 39.9)
    assert len(library) == count  # the original library is untouched


def test_materials_dialog_timber_has_no_sheet_width(qtbot: QtBot) -> None:
    dialog = MaterialsDialog(MaterialLibrary())
    qtbot.addWidget(dialog)
    for row in range(dialog.list.count()):
        dialog.list.setCurrentRow(row)
        if dialog.current_id() == "builtin.kvh_60x80":
            break
    assert not dialog.stock_width_spin.isEnabled()
    dialog.stock_length_spin.setValue(4000)
    kvh = dialog.library().materials["builtin.kvh_60x80"]
    assert (kvh.stock_length, kvh.stock_width, kvh.width) == (4000, None, 80)


def test_apply_library_offers_document_update(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def answer_yes(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(answer_yes))
    dialog = MaterialsDialog(window.controller.library, window)
    dialog.list.setCurrentRow(0)
    assert dialog.current_id() == MULTIPLEX
    dialog.price_spin.setValue(70)
    window.apply_library(dialog)
    assert "Multiplex" in asked[0]
    assert window.controller.doc.materials[MULTIPLEX].price == 70
    dialog.deleteLater()


# ====================================================================== properties
def test_properties_show_outdated_material(window: MainWindow) -> None:
    controller = window.controller
    controller.set_selection([board_id(controller.doc, "Seite_links")])
    panel = window.properties_panel
    assert panel.material_hint.isHidden()
    controller.set_library(changed_library(controller, price=1.0))
    assert not panel.material_hint.isHidden()
    panel.material_update_button.click()
    assert panel.material_hint.isHidden()
    panel.ignore_grain_check.click()
    assert controller.doc.boards[controller.selection[0]].ignore_grain


# ====================================================================== cutting plan
def test_cutplan_panel_shows_plans(qtbot: QtBot, controller: Controller) -> None:
    panel = CutplanPanel(controller)
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.table.rowCount() == 2
    names = {item.text() for r in range(2) if (item := panel.table.item(r, 0))}
    assert names == {"Multiplex Birke 18 mm", "HDF 3 mm"}
    assert panel.view.blocks
    assert "keine Preise" in panel.total_label.text()

    panel.kerf_spin.setValue(3)
    assert controller.doc.cut_settings.kerf == 3


def test_cutplan_pdf(tmp_path: Path, controller: Controller) -> None:
    path = tmp_path / "plan.pdf"
    write_pdf(path, optimize(controller.doc, controller.evaluation), "Schnittplan")
    assert path.read_bytes().startswith(b"%PDF")


# ====================================================================== export
@pytest.mark.parametrize("suffix", [".step", ".stl", ".obj"])
def test_export_from_window(window: MainWindow, tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"korpus{suffix}"
    assert window.export_to(path)
    assert path.stat().st_size > 0


def test_cutplan_dock_exists(window: MainWindow) -> None:
    assert window.cutplan_dock.windowTitle() == "Schnittplan"
