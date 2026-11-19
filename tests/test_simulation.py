"""Handles, fittings shown in 3D and the door / drawer simulation."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.model import front
from simpledesign.core.model.fitting import FittingKind
from simpledesign.core.model.machining import Hole, hardware_solids
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.texts import hardware_label
from simpledesign.ui.tools import SimulationTool

from .furniture import board_id, build_korpus
from .test_front import _add, _plan


def test_door_gets_a_handle_at_its_free_edge() -> None:
    doc = build_korpus()
    (door,), ev = _add(doc, _plan(doc, front.FrontKind.DOOR))
    (handle,) = [f for f in doc.fittings.values() if f.kind == FittingKind.HANDLE]
    assert (handle.part_id, handle.carrier_id) == (door, None)
    assert not ev.machining.errors
    state = ev.boards[door]
    shape = ev.machining.parts[door][0]
    holes = [f for f in shape.features if isinstance(f, Hole) and f.diameter == 5]
    assert len(holes) == 2  # through holes for the screws, 128 mm apart
    xs = {round(shape.origin[0] + h.center[0], 3) for h in holes}
    (x,) = xs
    assert x == pytest.approx(state.x + state.size[0] - 40)  # hinges left: handle right
    zs = sorted(shape.origin[2] + h.center[2] for h in holes)
    assert zs[1] - zs[0] == pytest.approx(128)
    (item,) = [h for h in ev.machining.hardware if h.kind == FittingKind.HANDLE]
    assert item.quantity == 1
    assert hardware_label(item) == "Möbelgriff BA 128 mm"
    doc.set_fitting_param(handle.id, "hole_spacing", 0)
    from simpledesign.core.model.evaluate import Evaluator

    ev = Evaluator().evaluate(doc)
    (item,) = [h for h in ev.machining.hardware if h.kind == FittingKind.HANDLE]
    assert hardware_label(item) == "Möbelknopf"


def test_drawer_handle_is_centred_and_slides_have_rails() -> None:
    doc = build_korpus()
    ids, ev = _add(doc, _plan(doc, front.FrontKind.DRAWER, drawers=2))
    solids = hardware_solids(ev.boards, doc.fittings.values())
    kinds = {s.kind for s in solids}
    assert kinds == {FittingKind.SLIDE, FittingKind.HANDLE}
    side = board_id(doc, "Seite_links")
    rails = [s for s in solids if s.kind == FittingKind.SLIDE and s.board_id == side]
    assert len(rails) == 2  # one per drawer
    bars = [s for s in solids if s.kind == FittingKind.HANDLE and s.axis == 0]
    assert len(bars) == 2
    state = ev.boards[ids[0]]
    for bar in bars:
        middle = (bar.lo[0] + bar.hi[0]) / 2
        assert middle == pytest.approx(state.x + state.size[0] / 2)
        assert bar.hi[1] <= state.y  # in front of the front


def test_hardware_and_simulation_in_window(qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        doc = build_korpus()
        doc.delete([board_id(doc, "Fachboden")], {})
        _add(doc, _plan(doc, front.FrontKind.DOOR, whole=True, leaves=2))
        win.controller._replace_document(doc, None)
        scene = win.scene
        assert scene.hardware and all(a.GetVisibility() for _s, a in scene.hardware)
        kinds = {s.kind for s, _a in scene.hardware}
        assert kinds == {FittingKind.HINGE, FittingKind.HANDLE}
        win.action_hardware.setChecked(False)
        assert not scene.hardware
        win.action_hardware.setChecked(True)
        door = board_id(win.controller.doc, "Tür")
        # the right leaf opens about its right edge, out to the front
        win.action_simulation.trigger()
        tool = scene.tool
        assert isinstance(tool, SimulationTool)
        motion = tool.motion_for(door, 1)
        assert motion is not None and motion.pivot is not None and motion.angle > 0
        face = scene.instance_actors(door, 1)[0]
        closed = face.GetBounds()
        win.viewport.show_view("front")
        win.viewport.render()
        state = win.controller.evaluation.boards[door]
        origin = state.instance_origins()[1]
        sx, sy = scene.to_display(
            (origin[0] + state.size[0] / 2, origin[1], origin[2] + state.size[2] / 2)
        )
        assert scene.instance_at(round(sx), round(sy)) == (door, 1)
        assert tool.press(round(sx), round(sy), False)
        tool.run_to_end()
        opened = face.GetBounds()
        assert opened[2] < closed[2] - 300  # swung out to the front (-Y)
        hardware = [a for s, a in scene.hardware if s.board_id == door and s.copy == 1]
        assert hardware and all(a.GetUserTransform() is not None for a in hardware)
        # a rebuild keeps the open state; another click closes it
        scene.rebuild()
        assert face.GetBounds()[2] == pytest.approx(opened[2])
        # click the open leaf where it is now
        b = face.GetBounds()
        ox, oy = scene.to_display(((b[0] + b[1]) / 2, b[2], (b[4] + b[5]) / 2))
        assert scene.instance_at(round(ox), round(oy)) == (door, 1)
        tool.press(round(ox), round(oy), False)
        tool.run_to_end()
        assert face.GetBounds()[2] == pytest.approx(closed[2])
        win.controller.undo_stack.clear()
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()


def test_drawer_simulation_pulls_the_whole_drawer(qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        doc = build_korpus()
        ids, _ev = _add(doc, _plan(doc, front.FrontKind.DRAWER))
        win.controller._replace_document(doc, None)
        win.action_simulation.trigger()
        tool = win.scene.tool
        assert isinstance(tool, SimulationTool)
        motion = tool.motion_for(ids[0], 0)
        assert motion is not None and motion.pivot is None
        assert {b for b, _c in motion.members} == set(ids)
        assert motion.travel[1] == pytest.approx(-345)  # the rail length
        before = win.scene.instance_actors(ids[1], 0)[0].GetBounds()
        motion.target = 1.0
        tool.motions[motion.members[0]] = motion
        tool._collect(motion)
        tool.timer.start()
        tool.run_to_end()
        after = win.scene.instance_actors(ids[1], 0)[0].GetBounds()
        assert after[2] == pytest.approx(before[2] - 345)
        win.scene.end_tool()
        assert win.scene.instance_actors(ids[1], 0)[0].GetUserTransform() is None
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
