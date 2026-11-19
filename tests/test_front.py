"""Door and drawer generator: fronts fitted into an opening of a carcass."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from simpledesign.core.geometry.collision import find_collisions
from simpledesign.core.model import fit, front
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.state import BoardState
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.tools import FrontTool

from .furniture import board_id, build_korpus

MATERIAL = "builtin.multiplex_birke_18"
POINT = (400.0, 397.0, 200.0)  # on the back panel, lowest compartment


def _evaluate(doc: Document) -> Evaluation:
    return Evaluator().evaluate(doc)


def _plan(doc: Document, kind: front.FrontKind, **settings: object) -> front.FrontPlan:
    ev = _evaluate(doc)
    comp = fit.compartment(doc, ev, POINT, 1, -1)
    assert comp is not None
    s = front.FrontSettings(**settings)  # type: ignore[arg-type]
    op = front.opening(doc, ev, comp, POINT, s.whole)
    assert op is not None
    t = front.Term("t", 18)
    if kind == front.FrontKind.DOOR:
        plan = front.plan_door(op, s, MATERIAL, t)
    else:
        plan = front.plan_drawer(op, s, MATERIAL, t, 12, 3)
    assert plan is not None
    return plan


def _add(doc: Document, plan: front.FrontPlan) -> tuple[list[str], Evaluation]:
    ids = front.create(doc, plan, None)
    ev = _evaluate(doc)
    assert not ev.errors
    # the preview shows what the formulas give
    for part, part_id in zip(plan.parts, ids, strict=True):
        state = ev.boards[part_id]
        origins = state.instance_origins()
        assert len(origins) == len(part.boxes)
        for (lo, size), origin in zip(part.boxes, origins, strict=True):
            assert origin == pytest.approx(lo)
            assert state.size == pytest.approx(size)
    assert not find_collisions(ev.machining.parts)
    return ids, ev


def _end(state: BoardState, axis: int) -> float:
    return state.origin[axis] + state.size[axis]


def _lowest_shelf_middle(doc: Document, ev: Evaluation) -> float:
    state = ev.boards[board_id(doc, "Fachboden")]
    return min(o[2] for o in state.instance_origins()) + state.thickness / 2


def test_overlay_door_covers_half_of_the_parts_around() -> None:
    doc = build_korpus()
    (door,), ev = _add(doc, _plan(doc, front.FrontKind.DOOR))
    state = ev.boards[door]
    assert doc.boards[door].name == "Tür"
    assert (state.x, state.y, state.z) == pytest.approx((1.5, -18, 1.5))
    assert _end(state, 0) == pytest.approx(798.5)
    assert _end(state, 2) == pytest.approx(_lowest_shelf_middle(doc, ev) - 1.5)
    assert doc.display_expression(door, "thickness") == "t"
    # bound to the carcass: a wider cabinet gives a wider door
    doc.set_expression(next(p.id for p in doc.parameters.values() if p.name == "B"), None, "1000")
    assert _end(_evaluate(doc).boards[door], 0) == pytest.approx(998.5)


def test_inset_door_sits_inside_the_opening() -> None:
    doc = build_korpus()
    (door,), ev = _add(doc, _plan(doc, front.FrontKind.DOOR, mounting=front.Mounting.INSET))
    state = ev.boards[door]
    assert (state.x, state.y, state.z) == pytest.approx((21, 0, 21))
    assert _end(state, 0) == pytest.approx(779)


def test_two_leaves_over_the_whole_cabinet() -> None:
    doc = build_korpus()
    plan = _plan(doc, front.FrontKind.DOOR, whole=True, leaves=2)
    (door,), ev = _add(doc, plan)
    state = ev.boards[door]
    assert len(state.instance_origins()) == 2
    assert state.size[0] == pytest.approx((797 - 3) / 2)
    assert (state.z, _end(state, 2)) == pytest.approx((1.5, 1798.5))


@pytest.mark.parametrize("drawers", [1, 3])
def test_drawer_box_fits_behind_the_front(drawers: int) -> None:
    doc = build_korpus()
    plan = _plan(doc, front.FrontKind.DRAWER, drawers=drawers)
    ids, ev = _add(doc, plan)
    names = [doc.boards[i].name for i in ids]
    assert names == [
        "Schubladenfront",
        "Schubladenboden",
        "Zarge_links",
        "Zarge_rechts",
        "Zarge_vorne",
        "Zarge_hinten",
    ]
    (group,) = {doc.boards[i].group_id for i in ids}
    assert group is not None and doc.groups[group].name == "Schublade"
    for i in ids:
        assert len(ev.boards[i].instance_origins()) == drawers
    sides = [ev.boards[i] for i in ids[2:4]]
    # room for the slides on both sides
    inner_left = _end(ev.boards[board_id(doc, "Seite_links")], 0)
    assert sides[0].x - inner_left == pytest.approx(13)


def test_front_tool_in_window(qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        win.scene.snapping_enabled = False
        doc = build_korpus()
        doc.delete([board_id(doc, "Fachboden")], {})
        win.controller._replace_document(doc, None)
        win.viewport.show_view("front")
        win.viewport.render()
        c = win.controller
        win.action_insert_drawer.trigger()
        tool = win.scene.tool
        assert isinstance(tool, FrontTool)
        sx, sy = win.scene.to_display((402, 400, 900))
        x, y = round(sx), round(sy)
        tool.hover(x, y)
        assert tool.plan is not None and tool.preview.GetVisibility()
        assert tool.type_key(0, "2")
        assert c.front_settings.drawers == 2
        assert tool.type_key(Qt.Key.Key_L, "l")
        assert c.front_settings.mounting == front.Mounting.INSET
        assert not tool.type_key(Qt.Key.Key_G, "g")  # whole cabinet: doors only
        count = c.undo_stack.count()
        assert tool.press(x, y, False) is True
        assert c.undo_stack.count() == count + 1
        (new,) = c.selection
        assert c.doc.boards[new].name == "Schubladenfront"
        assert len(c.evaluation.boards[new].instance_origins()) == 2
        assert not c.evaluation.errors
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
