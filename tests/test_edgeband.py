"""Edge banding: rough size, running metres, cut list, file, drawing, panel."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.cutlist.cutlist import band_demand, build_cutlist, edges_summary
from simpledesign.core.io import sdz
from simpledesign.core.materials.edgeband import EDGE_EXCESS, BandEdge, EdgeBand
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.part import Orientation
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.texts import edge_label

ABS2 = "builtin.abs_2_white"


def _board(material: str = "builtin.multiplex_birke_18") -> tuple[Document, str]:
    doc = Document()
    board = doc.add_board("Brett", material_id=material)
    doc.set_expression(board.id, "length", "600")
    doc.set_expression(board.id, "width", "300")
    return doc, board.id


def _rows(doc: Document) -> list[tuple[float, float, tuple[tuple[BandEdge, str], ...]]]:
    rows = build_cutlist(doc, Evaluator().evaluate(doc))
    return [(r.length, r.width, r.edges) for r in rows]


def test_banding_reduces_the_rough_size() -> None:
    doc, board = _board()
    doc.set_edge_band(board, [BandEdge.L1, BandEdge.L2], ABS2)
    doc.set_edge_band(board, [BandEdge.W1], "builtin.veneer_oak")
    ((length, width, edges),) = _rows(doc)
    assert length == pytest.approx(600 - 0.5)
    assert width == pytest.approx(300 - 4)
    assert edges_summary(edges) == "L1, L2: ABS 2 mm weiß; W1: Furnier Eiche 0,5 mm"


def test_different_banding_is_not_merged() -> None:
    doc, a = _board()
    b = doc.duplicate([a])[0]
    assert len(_rows(doc)) == 1
    doc.set_edge_band(b, [BandEdge.W1], "builtin.abs_04_white")
    assert len(_rows(doc)) == 2
    doc.set_edge_band(b, [BandEdge.W1], None)
    assert len(_rows(doc)) == 1


def test_turning_without_grain_swaps_the_edges() -> None:
    doc, board = _board("builtin.mdf_19")
    doc.set_expression(board, "length", "300")
    doc.set_expression(board, "width", "600")
    doc.set_edge_band(board, [BandEdge.L1], ABS2)  # along the 300 side
    ((length, width, edges),) = _rows(doc)
    assert length == pytest.approx(598)
    assert width == pytest.approx(300)
    assert [edge for edge, _ in edges] == [BandEdge.W1]


def test_running_metres() -> None:
    doc, board = _board()
    doc.set_expression(board, "count", "2")
    doc.set_edge_band(board, list(BandEdge), ABS2)
    (demand,) = band_demand(doc, Evaluator().evaluate(doc))
    assert demand.band_id == ABS2
    assert demand.edges == 8
    assert demand.length == pytest.approx(2 * (2 * 600 + 2 * 300 + 4 * EDGE_EXCESS))


def test_file_round_trip_with_own_band(tmp_path: Path) -> None:
    doc, board = _board()
    own = EdgeBand("own1", "Anleimer Nuss 8 mm", 8.0, "#5a3a22")
    doc.set_edge_band(board, [BandEdge.L1], own)
    doc.set_edge_band(board, [BandEdge.W2], ABS2)
    path = tmp_path / "band.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert loaded.boards[board].edges == {BandEdge.L1: "own1", BandEdge.W2: ABS2}
    assert loaded.edge_band("own1") == own
    assert _rows(loaded) == _rows(doc)


def test_copy_takes_own_bands_along() -> None:
    source, board = _board()
    own = EdgeBand("own1", "Eigen", 1.5)
    source.set_edge_band(board, [BandEdge.L2], own)
    target = Document()
    (copy,) = target.copy_from(source, [board])
    assert target.boards[copy].edges == {BandEdge.L2: "own1"}
    assert target.edge_band("own1") == own


def test_edge_labels_name_the_side() -> None:
    assert edge_label(Orientation.XY, BandEdge.L1) == "Vorne (L1)"
    assert edge_label(Orientation.XY, BandEdge.L2) == "Hinten (L2)"
    assert edge_label(Orientation.XY, BandEdge.W1) == "Links (W1)"


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def test_properties_panel_sets_the_band(window: MainWindow) -> None:
    doc, board = _board()
    c = window.controller
    c._replace_document(doc, None)
    c.set_selection([board])
    panel = window.properties_panel
    combo = panel.edge_combos[None]
    combo.activated.emit(combo.findData(ABS2))  # as if picked by the user
    assert c.doc.boards[board].edges == dict.fromkeys(BandEdge, ABS2)
    front = panel.edge_combos[BandEdge.L1]
    front.activated.emit(front.findData(""))
    assert BandEdge.L1 not in c.doc.boards[board].edges
    assert panel.edge_combos[None].currentData() is None  # mixed
    c.undo_stack.undo()
    assert len(c.doc.boards[board].edges) == 4


def test_part_drawing_lists_and_marks_the_banding() -> None:
    from simpledesign.core.drawing.layout import DrawingOptions, build_sheets
    from simpledesign.core.drawing.sheet import Polyline, Style
    from simpledesign.ui.drawing_dialog import drawing_labels

    doc, board = _board()
    labels = drawing_labels()
    options = DrawingOptions(overview=False)
    plain = build_sheets(doc, Evaluator().evaluate(doc), labels, options, "T", "d")
    doc.set_edge_band(board, [BandEdge.L1, BandEdge.W2], ABS2)
    (sheet,) = build_sheets(doc, Evaluator().evaluate(doc), labels, options, "T", "d")
    assert ("Kanten", "L1, W2: ABS 2 mm weiß") in sheet.fields

    def visible(items: Iterable[object]) -> int:
        return sum(isinstance(i, Polyline) and i.style is Style.VISIBLE for i in items)

    assert visible(sheet.items) == visible(plain[0].items) + 2
