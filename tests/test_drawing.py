"""Phase 6: technical drawings (projection, layout, PDF and DXF)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from ezdxf import units
from ezdxf.filemanagement import readfile
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.drawing.layout import DrawingOptions, build_sheets, part_views
from simpledesign.core.drawing.projection import STANDARD_VIEWS, ViewKind, project
from simpledesign.core.drawing.sheet import (
    Dimension,
    PaperSize,
    Polyline,
    Text,
    dimension_text,
    expand,
    pick_scale,
    scale_text,
)
from simpledesign.core.geometry.mesh import make_shape
from simpledesign.core.io.dxf import write_dxf
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.machining import Hole, PartShape
from simpledesign.core.model.part import Orientation
from simpledesign.core.templates.builtin import builtin_templates
from simpledesign.ui.drawing_dialog import DrawingDialog, drawing_labels
from simpledesign.ui.main_window import MainWindow


def test_projection_bounds_of_a_box() -> None:
    shape = make_shape((100.0, 20.0, 50.0))
    front = project([shape], STANDARD_VIEWS[ViewKind.FRONT])
    (u0, v0), (u1, v1) = front.bounds()
    assert (u1 - u0, v1 - v0) == pytest.approx((100, 50))
    left = project([shape], STANDARD_VIEWS[ViewKind.LEFT])
    (u0, v0), (u1, v1) = left.bounds()
    assert (u1 - u0, v1 - v0) == pytest.approx((20, 50))
    assert not front.hidden  # a plain box hides nothing


def test_hidden_edges_and_circles() -> None:
    hole = Hole((30.0, 10.0, 18.0), 2, -1, 8.0, 10.0)
    shape = make_shape((100.0, 50.0, 18.0), (hole,))
    top = project([shape], STANDARD_VIEWS[ViewKind.TOP])
    assert len(top.circles) == 1
    assert top.circles[0].radius == pytest.approx(4)
    front = project([shape], STANDARD_VIEWS[ViewKind.FRONT])
    assert front.hidden  # the blind hole shows as hidden lines from the front
    assert not project([shape], STANDARD_VIEWS[ViewKind.FRONT], hidden=False).hidden


def test_part_views_look_at_the_face() -> None:
    shape = PartShape((0.0, 0.0, 0.0), (18.0, 400.0, 700.0))
    face, above, left = part_views(shape, Orientation.ZY)  # side panel
    solid = make_shape(shape.size)
    (u0, v0), (u1, v1) = project([solid], face).bounds()
    assert (u1 - u0, v1 - v0) == pytest.approx((700, 400))  # length right, width up
    (u0, v0), (u1, v1) = project([solid], above).bounds()
    assert (u1 - u0, v1 - v0) == pytest.approx((700, 18))
    (u0, v0), (u1, v1) = project([solid], left).bounds()
    assert (u1 - u0, v1 - v0) == pytest.approx((18, 400))


def test_scale_and_texts() -> None:
    assert pick_scale(0.3) == pytest.approx(0.2)  # 1:5
    assert pick_scale(1.7) == 1.0
    assert scale_text(0.1) == "1:10" and scale_text(2.0) == "2:1"
    assert dimension_text(18.0) == "18" and dimension_text(12.26) == "12,3"


def test_dimension_expands_to_lines_and_text() -> None:
    prims = expand(Dimension((0.0, 0.0), (50.0, 0.0), 0, 10.0, "500"))
    texts = [p for p in prims if isinstance(p, Text)]
    assert [t.text for t in texts] == ["500"]
    assert texts[0].pos[0] == pytest.approx(25)
    assert sum(isinstance(p, Polyline) for p in prims) == 2 + 1 + 4  # ext, line, arrows
    short = expand(Dimension((0.0, 0.0), (0.0, 2.0), 1, -8.0, "18"))
    (text,) = [p for p in short if isinstance(p, Text)]
    assert text.rotation == 90 and text.pos[1] > 2  # value moved beyond the line


@pytest.mark.parametrize("index", range(len(builtin_templates())))
def test_sheets_for_templates(index: int, tmp_path: Path) -> None:
    template = builtin_templates()[index]
    doc = template.document()
    evaluation = Evaluator().evaluate(doc)
    sheets = build_sheets(
        doc, evaluation, drawing_labels(), DrawingOptions(PaperSize.A3), "T", "1.1.2027"
    )
    assert len(sheets) >= 2
    for sheet in sheets:
        w, h = sheet.size
        for item in sheet.items:
            for prim in expand(item):
                points = prim.points if isinstance(prim, Polyline) else (prim.pos,)
                for x, y in points:
                    assert -1 <= x <= w + 1 and -1 <= y <= h + 1, (sheet.title, prim)
    path = tmp_path / "t.dxf"
    write_dxf(sheets, path)
    dxf = readfile(path)
    layers = {e.dxf.layer for e in dxf.modelspace()}
    assert {"SICHTBAR", "MASS", "TEXT", "RAHMEN"} <= layers


def test_options_select_sheets() -> None:
    doc = builtin_templates()[0].document()
    evaluation = Evaluator().evaluate(doc)
    labels = drawing_labels()
    only = build_sheets(doc, evaluation, labels, DrawingOptions(parts=False), "T", "d")
    assert len(only) == 1
    assert ("Blatt", "1 von 1") in only[0].fields or any(
        isinstance(i, Text) and i.text == "1 von 1" for i in only[0].items
    )
    parts = build_sheets(doc, evaluation, labels, DrawingOptions(overview=False), "T", "d")
    assert all(s.title != "T" for s in parts)


# ------------------------------------------------------------------ dialog
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


def test_drawing_dialog_pages_and_export(window: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    ctrl = window.controller
    ctrl.add_board()
    ctrl.add_board()
    dialog = window.show_drawing()
    assert isinstance(dialog, DrawingDialog)
    qtbot.waitExposed(dialog)
    assert len(dialog.sheets) == 2  # overview + one part (both boards are equal)
    assert not dialog.prev_button.isEnabled() and dialog.next_button.isEnabled()
    dialog.next_button.click()
    assert dialog.page == 1 and dialog.view.sheet is dialog.sheets[1]
    dialog.parts_check.setChecked(False)
    assert len(dialog.sheets) == 1 and dialog.page == 0
    pdf = tmp_path / "z.pdf"
    assert dialog.save(pdf)
    assert pdf.read_bytes().startswith(b"%PDF")
    dxf = tmp_path / "z.dxf"
    assert dialog.save(dxf)
    assert readfile(dxf).units == units.MM
    dialog.reject()
