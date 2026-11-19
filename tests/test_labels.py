"""Part labels (Etiketten)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtPdf import QPdfDocument
from pytestqt.qtbot import QtBot

from simpledesign.core.cutlist.cutlist import build_cutlist
from simpledesign.core.cutlist.labels import A4, LABEL_SHEETS, build_labels, label_sheet, qr_matrix
from simpledesign.core.materials.edgeband import BUILTIN_EDGE_BANDS, BandEdge
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.ui.labels_dialog import label_lines, pages, write_labels_pdf
from simpledesign.ui.main_window import MainWindow

from .furniture import board_id, build_korpus

THIN, THICK = BUILTIN_EDGE_BANDS[0], BUILTIN_EDGE_BANDS[2]


def _banded() -> tuple[Document, Evaluation]:
    doc = build_korpus()
    shelf = doc.boards[board_id(doc, "Fachboden")]
    shelf.edges[BandEdge.L1] = THIN.id
    shelf.edges[BandEdge.W2] = THICK.id
    return doc, Evaluator().evaluate(doc)


def test_one_label_per_part_in_cut_list_order() -> None:
    doc, ev = _banded()
    labels = build_labels(doc, ev, "Regal")
    rows = build_cutlist(doc, ev)
    assert len(labels) == sum(r.quantity for r in rows)
    assert [label.position for label in labels] == sorted(label.position for label in labels)
    shelves = [label for label in labels if label.name == "Fachboden"]
    assert [(s.copy, s.copies) for s in shelves] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    row = rows[shelves[0].position - 1]
    assert "Fachboden" in row.names and row.quantity == 4
    shelf = shelves[0]
    assert shelf.rough == pytest.approx((row.length, row.width, row.thickness))
    # finished = rough plus the banding
    assert shelf.finished[0] - shelf.rough[0] == pytest.approx(THICK.thickness)
    assert shelf.finished[1] - shelf.rough[1] == pytest.approx(THIN.thickness)
    assert shelf.furniture == "Regal" and shelf.has_grain
    text = shelf.qr_text()
    assert "Fachboden" in text and "Pos. " in text and THIN.name in text


def test_turned_part_without_grain_and_selection() -> None:
    doc = build_korpus()
    back = board_id(doc, "Rückwand")
    doc.boards[back].edges[BandEdge.L1] = THICK.id
    ev = Evaluator().evaluate(doc)
    (label,) = build_labels(doc, ev, "", {back})
    assert not label.has_grain
    assert sorted(label.finished[:2]) == pytest.approx([800, 1800])
    added = (label.finished[0] - label.rough[0], label.finished[1] - label.rough[1])
    assert sorted(added) == pytest.approx([0, THICK.thickness])
    assert label.furniture == ""


def test_sheets_fit_on_a4_and_pages_skip_used_labels() -> None:
    for sheet in LABEL_SHEETS:
        x, y = sheet.position(sheet.per_sheet - 1)
        assert sheet.position(0)[0] >= 0 and sheet.position(0)[1] >= 0
        assert x + sheet.width <= A4[0] + 0.01 and y + sheet.height <= A4[1] + 0.01
    assert label_sheet("unknown") == LABEL_SHEETS[0]
    doc, ev = _banded()
    labels = build_labels(doc, ev, "Regal")  # 9 parts
    sheet = label_sheet("a4-2x5-105x57")  # 10 per sheet
    split = pages(sheet, labels, 6)
    assert [len(p) for p in split] == [4, 5]
    assert [slot for slot, _ in split[0]] == [6, 7, 8, 9]
    assert split[1][0] == (0, labels[4])
    assert pages(sheet, [], 3) == []


def test_qr_code_and_pdf(tmp_path: Path, qtbot: QtBot) -> None:
    matrix = qr_matrix("Seite_links\n1800 × 400 × 18 mm")
    assert len(matrix) >= 21 and all(len(row) == len(matrix) for row in matrix)
    assert matrix[0][:7] == [True] * 7  # finder pattern
    doc, ev = _banded()
    labels = build_labels(doc, ev, "Regal")
    shelf = next(label for label in labels if label.edges)
    assert label_lines(shelf, short_edges=True)[-1] == "Kanten: L1, W2"
    path = tmp_path / "e.pdf"
    write_labels_pdf(path, labels, label_sheet("a4-2x5-105x57"), skip=6, frames=True)
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    document = QPdfDocument()
    document.load(str(path))
    assert document.pageCount() == 2


def test_labels_dialog(tmp_path: Path, qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        doc, _ev = _banded()
        win.controller._replace_document(doc, None)
        win.controller.set_selection([board_id(doc, "Fachboden")])
        dialog = win.show_labels()
        assert dialog.selected_check.isChecked()
        assert len(dialog.labels) == 4
        dialog.selected_check.setChecked(False)
        assert len(dialog.labels) == 9
        dialog.sheet_combo.setCurrentIndex(dialog.sheet_combo.findData("a4-2x5-105x57"))
        assert dialog.skip_spin.maximum() == 10
        dialog.skip_spin.setValue(7)
        assert "2 Bögen" in dialog.count_label.text()
        assert len(dialog.preview.page) == 4
        dialog.preview.repaint()
        path = tmp_path / "labels.pdf"
        assert dialog.export_to(path)
        assert path.stat().st_size > 1000
        dialog.close()
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
