"""Phase 5: the sketch editor (Qt-free session and the dialog)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.app.sketch_session import EditProblem, SketchEditError, SketchSession, Snap
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.sketch.model import ORIGIN, Line, Sketch
from simpledesign.core.sketch.model import ConstraintKind as K
from simpledesign.core.sketch.solver import References, Status
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.sketch_editor import SketchEditor, Tool


def value_of(text: str) -> float:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        raise FormulaError(ErrorCode.SYNTAX) from None


def session() -> SketchSession:
    return SketchSession(Sketch(), {}, References.for_plane(), value_of)


def rectangle(s: SketchSession) -> list[Line]:
    return s.add_rectangle(Snap((0.0, 0.0), ORIGIN), Snap((100.0, 50.0)))


def test_draw_rectangle_closes_profile() -> None:
    s = session()
    lines = rectangle(s)
    assert len(lines) == 4
    assert s.profile_problem is None and len(s.regions) == 1
    assert s.valid
    assert s.result.status == Status.UNDERDETERMINED


def test_dimensions_fully_define_and_follow_formulas() -> None:
    s = session()
    lines = rectangle(s)
    s.selection = [lines[0].id]
    (width,) = s.constrain(None)
    assert s.formulas[width.id] == "100"
    s.selection = [lines[1].id]
    (height,) = s.constrain(None)
    assert s.result.status == Status.SOLVED
    s.set_formula(width.id, "240")
    assert s.measure(width) == pytest.approx(240, abs=1e-6)
    assert s.measure(height) == pytest.approx(50, abs=1e-6)


def test_redundant_and_bad_input_are_rolled_back() -> None:
    s = session()
    lines = rectangle(s)
    s.selection = [lines[0].id]
    with pytest.raises(SketchEditError) as info:
        s.constrain(K.HORIZONTAL)  # already horizontal
    assert info.value.problem == EditProblem.REDUNDANT
    assert len(s.sketch.constraints) == 5  # 4 H/V + coincident
    s.selection = []
    with pytest.raises(SketchEditError) as info:
        s.constrain(K.PARALLEL)
    assert info.value.problem == EditProblem.SELECTION
    s.selection = [lines[0].id]
    (dim,) = s.constrain(None)
    with pytest.raises(SketchEditError) as info:
        s.set_formula(dim.id, "abc")
    assert info.value.problem == EditProblem.FORMULA
    assert s.formulas[dim.id] == "100"


def test_open_line_chain_is_invalid() -> None:
    s = session()
    s.add_line(Snap((0.0, 0.0)), Snap((50.0, 1.0)))
    assert not s.valid
    assert s.profile_problem is not None
    line = next(iter(s.sketch.entities.values()))
    assert any(c.kind == K.HORIZONTAL for c in s.sketch.constraints.values())
    assert isinstance(line, Line)


def test_undo_redo_and_delete() -> None:
    s = session()
    rectangle(s)
    s.add_circle(Snap((50.0, 25.0)), 10)
    assert len(s.regions[0].holes) == 1
    s.undo()
    assert not any(e for e in s.sketch.entities if e.startswith("c"))  # circle gone
    assert len(s.sketch.entities) == 4
    s.redo()
    assert len(s.sketch.entities) == 5
    s.selection = [next(iter(s.sketch.entities))]
    assert s.delete_selection()
    assert s.profile_problem is not None


def test_drag_point_moves_geometry() -> None:
    s = session()
    lines = rectangle(s)
    corner = lines[1].end  # (100, 50)
    s.checkpoint()
    assert s.drag(corner, (150.0, 80.0))
    u, v = s.positions()[corner]
    assert u == pytest.approx(150, abs=1e-3) and v == pytest.approx(80, abs=1e-3)
    origin_corner = lines[0].start
    assert s.positions()[origin_corner] == pytest.approx((0, 0), abs=1e-6)


def test_pick_prefers_points() -> None:
    s = session()
    lines = rectangle(s)
    assert s.pick((100.2, 0.1), 1.0) == lines[0].end
    assert s.pick((50.0, 0.3), 1.0) == lines[0].id
    assert s.pick((50.0, 25.0), 1.0) is None


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


def click(qtbot: QtBot, editor: SketchEditor, model: tuple[float, float]) -> None:
    point = editor.canvas.to_screen(model).toPoint()
    QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=point)


def test_new_sketch_part_by_mouse(window: MainWindow, qtbot: QtBot) -> None:
    editor = window.add_sketch_part()
    qtbot.waitExposed(editor)
    ok = editor.buttons.button(editor.buttons.StandardButton.Ok)
    assert not ok.isEnabled()  # empty sketch
    editor.set_tool(Tool.RECTANGLE)
    click(qtbot, editor, (0.0, 0.0))
    click(qtbot, editor, (40.0, 30.0))
    assert len(editor.sketch.entities) == 4
    assert ok.isEnabled()
    assert editor.table.rowCount() >= 5
    editor.session.selection = [editor.sketch.entities.__iter__().__next__()]
    assert editor.constrain(None)
    formula_items = [editor.table.item(r, 1) for r in range(editor.table.rowCount())]
    editable = [
        i for i in formula_items if i is not None and i.flags() & Qt.ItemFlag.ItemIsEditable
    ]
    editable[0].setText("80")
    ok.click()
    (board_id,) = window.controller.selection
    assert window.controller.evaluation.value(board_id, "length") == pytest.approx(80, abs=1e-3)
    # The properties panel shows the sketch section; length is read-only.
    panel = window.properties_panel
    assert panel.profile_button.text().startswith("Kontur bearbeiten")
    assert panel.fields["length"].edit.isReadOnly()


def test_line_tool_chains_and_closes(window: MainWindow, qtbot: QtBot) -> None:
    editor = window.add_sketch_part()
    qtbot.waitExposed(editor)
    for p in ((0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 0.0)):
        click(qtbot, editor, p)
    assert len(editor.sketch.entities) == 3
    assert editor.session.profile_problem is None
    assert editor.canvas.pending == []  # loop closed: chain ended
    editor.reject()


def test_cut_dialog_from_properties(window: MainWindow, qtbot: QtBot) -> None:
    ctrl = window.controller
    board_id = ctrl.add_board()
    ctrl.set_selection([board_id])
    editor = window.properties_panel.add_cut()
    assert editor is not None
    qtbot.waitExposed(editor)
    click(qtbot, editor, (100.0, 50.0))
    click(qtbot, editor, (200.0, 100.0))
    editor.depth_edit.setText("x")
    ok = editor.buttons.button(editor.buttons.StandardButton.Ok)
    assert not ok.isEnabled()
    editor.depth_edit.setText("8")
    assert ok.isEnabled()
    ok.click()
    assert len(ctrl.doc.boards[board_id].cuts) == 1
    panel = window.properties_panel
    assert panel.cut_list.count() == 1
    panel.cut_list.setCurrentRow(0)
    panel.delete_cut_button.click()
    assert not ctrl.doc.boards[board_id].cuts


def test_canvas_wheel_zoom_keeps_point(window: MainWindow, qtbot: QtBot) -> None:
    editor = window.add_sketch_part()
    qtbot.waitExposed(editor)
    canvas = editor.canvas
    before = canvas.to_model(canvas.rect().center().toPointF())
    scale = canvas.scale
    canvas.scale *= 2  # zoom is applied around the cursor in wheelEvent
    assert canvas.scale != scale
    canvas.scale = scale
    assert canvas.to_model(canvas.rect().center().toPointF()) == pytest.approx(before)
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    assert canvas.tool == Tool.SELECT or canvas.pending == []
    editor.reject()
