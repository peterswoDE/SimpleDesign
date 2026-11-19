"""Variants: named parameter sets, switching, file format, per-variant export."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.io import sdz
from simpledesign.core.model import variants
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.ui.main_window import MainWindow

from .furniture import board_id, build_korpus, param_id


def _width(doc: Document) -> float:
    return Evaluator().evaluate(doc).value(param_id(doc, "B"))


def test_add_switch_rename_remove() -> None:
    doc = build_korpus()
    variants.add(doc, "Breit")
    assert list(doc.variants) == ["Standard", "Breit"] and doc.active_variant == "Breit"
    doc.set_expression(param_id(doc, "B"), None, "1200")
    variants.switch(doc, "Standard")
    assert _width(doc) == 800
    variants.switch(doc, "Breit")
    assert _width(doc) == 1200
    with pytest.raises(ValueError):
        variants.add(doc, "Standard")
    with pytest.raises(ValueError):
        variants.add(doc, "  ")
    variants.rename(doc, "Breit", "XL")
    assert doc.active_variant == "XL" and list(doc.variants) == ["Standard", "XL"]
    variants.remove(doc, "XL")  # the active one: back to Standard
    assert doc.active_variant == "Standard" and _width(doc) == 800
    variants.remove(doc, "Standard")
    assert not doc.variants and doc.active_variant is None and _width(doc) == 800


def test_formulas_and_deleted_references() -> None:
    doc = build_korpus()
    variants.add(doc, "Formel")
    doc.add_parameter("Extra", "50")
    doc.set_expression(param_id(doc, "B"), None, "2 * T + Extra")
    variants.switch(doc, "Standard")
    # a parameter the variant does not know keeps its value
    assert Evaluator().evaluate(doc).value(param_id(doc, "Extra")) == 50
    assert _width(doc) == 800
    # the variant refers to Extra, which is then deleted: B stays as it is
    doc.delete([param_id(doc, "Extra")], {})
    variants.switch(doc, "Formel")
    assert _width(doc) == 800
    # a copy with another variant loaded leaves the document alone
    doc.set_expression(param_id(doc, "B"), None, "900")
    other = variants.variant_document(doc, "Standard")
    assert _width(other) == 800 and _width(doc) == 900


def test_file_round_trip(tmp_path: Path) -> None:
    doc = build_korpus()
    variants.add(doc, "Hoch")
    doc.set_expression(param_id(doc, "H"), None, "2200")
    path = tmp_path / "v.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert loaded.active_variant == "Hoch"
    assert set(loaded.variants) == {"Standard", "Hoch"}
    assert loaded.display_expression(param_id(loaded, "H")) == "2200"
    variants.switch(loaded, "Standard")
    assert loaded.display_expression(param_id(loaded, "H")) == "1800"
    # files from before variants open without them
    data = sdz.to_dict(build_korpus())
    data["format_version"] = 5
    del data["variants"], data["active_variant"]
    old = sdz.from_dict(data)
    assert old.variants == {} and old.active_variant is None


def test_variants_in_the_window(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QInputDialog

    win = MainWindow()
    try:
        win.controller._replace_document(build_korpus(), None)
        panel = win.parameters_panel
        assert not panel.variant_combo.isEnabled()
        assert not win.cutlist_panel.variants_action.isEnabled()
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Breit", True))
        panel.add_variant()
        doc = win.controller.doc
        assert doc.active_variant == "Breit" and panel.variant_combo.currentText() == "Breit"
        assert win.cutlist_panel.variants_action.isEnabled()
        win.controller.set_expression(param_id(doc, "B"), None, "1200")
        bottom = board_id(win.controller.doc, "Boden")
        assert win.controller.evaluation.boards[bottom].size[0] == pytest.approx(1164)

        panel.variant_combo.setCurrentIndex(0)
        panel._on_variant_chosen(0)
        assert win.controller.doc.active_variant == "Standard"
        assert win.controller.evaluation.boards[bottom].size[0] == pytest.approx(764)
        win.controller.undo_stack.undo()
        assert win.controller.doc.active_variant == "Breit"

        written = win.cutlist_panel.export_variants_to(tmp_path)
        names = sorted(p.name for p in written)
        assert "Unbenannt_Breit.csv" in names and "Unbenannt_Standard.csv" in names
        assert any(n.endswith("_Schnittplan.pdf") for n in names)
        wide = (tmp_path / "Unbenannt_Breit.csv").read_text(encoding="utf-8-sig")
        narrow = (tmp_path / "Unbenannt_Standard.csv").read_text(encoding="utf-8-sig")
        assert "1164" in wide and "1164" not in narrow
        # exporting does not change the document
        assert win.controller.doc.active_variant == "Breit"
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
