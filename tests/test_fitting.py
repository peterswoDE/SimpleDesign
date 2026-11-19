"""Fittings with drilling pattern: hinges, drawer slides, shelf pin rows."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.io import sdz
from simpledesign.core.model import fitting, front
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.fitting import FittingKind
from simpledesign.core.model.machining import Hole, JointErrorCode
from simpledesign.ui.main_window import MainWindow
from simpledesign.ui.texts import hardware_label
from simpledesign.ui.tools import ShelfPinTool

from .furniture import board_id, build_korpus
from .test_front import _add, _plan


def _evaluate(doc: Document) -> Evaluation:
    return Evaluator().evaluate(doc)


def _holes(ev: Evaluation, board: str, diameter: float, copy: int = 0) -> list[Hole]:
    """World holes of one copy of ``board`` with ``diameter``."""
    shape = ev.machining.parts[board][copy]
    result = []
    for f in shape.features:
        if isinstance(f, Hole) and f.diameter == diameter:
            centre = tuple(shape.origin[i] + f.center[i] for i in range(3))
            result.append(
                Hole((centre[0], centre[1], centre[2]), f.axis, f.direction, f.diameter, f.depth)
            )
    return sorted(result, key=lambda h: h.center[2])


def test_hinge_count_and_slide_length_rules() -> None:
    assert [fitting.hinge_count(h) for h in (600, 900, 1200, 2000, 2400)] == [2, 2, 3, 4, 5]
    assert fitting.slide_length(372) == 350
    assert fitting.slide_length(650) == 650
    assert fitting.slide_length(200) is None


def test_door_gets_hinges_on_the_left_side() -> None:
    doc = build_korpus()
    (door,), ev = _add(doc, _plan(doc, front.FrontKind.DOOR))
    (hinge,) = doc.fittings.values()
    side = board_id(doc, "Seite_links")
    assert (hinge.kind, hinge.part_id, hinge.carrier_id) == (FittingKind.HINGE, door, side)
    assert not ev.machining.errors
    state = ev.boards[door]
    cups = _holes(ev, door, 35)
    assert len(cups) == 2  # a low door: two hinges
    for cup in cups:
        # cup on the back of the door, 21.5 mm from its left edge
        assert cup.center[0] == pytest.approx(state.x + 21.5)
        assert cup.center[1] == pytest.approx(state.y + state.size[1])
        assert (cup.axis, cup.direction, cup.depth) == (1, -1, 13)
    assert cups[0].center[2] == pytest.approx(state.z + 100)
    assert cups[1].center[2] == pytest.approx(state.z + state.size[2] - 100)
    plate = _holes(ev, side, 5)
    assert len(plate) == 4
    assert all(h.center[0] == pytest.approx(18) for h in plate)  # inner face of the side
    assert all(h.center[1] == pytest.approx(37) for h in plate)
    assert plate[1].center[2] - plate[0].center[2] == pytest.approx(32)
    (item,) = ev.machining.hardware
    assert item.quantity == 2
    assert hardware_label(item) == "Topfband 35 mm mit Montageplatte"


def test_pair_of_doors_hinges_on_both_sides() -> None:
    doc = build_korpus()
    (door,), ev = _add(doc, _plan(doc, front.FrontKind.DOOR, whole=True, leaves=2))
    carriers = {f.carrier_id for f in doc.fittings.values()}
    assert carriers == {board_id(doc, "Seite_links"), board_id(doc, "Seite_rechts")}
    assert not ev.machining.errors
    (item,) = ev.machining.hardware
    assert item.quantity == 2 * 4  # 1797 mm high: four hinges per leaf
    right_leaf = ev.boards[door].instance_origins()[1]
    cups = _holes(ev, door, 35, copy=1)
    assert len(cups) == 4
    assert cups[0].center[0] == pytest.approx(right_leaf[0] + ev.boards[door].size[0] - 21.5)


def test_hinge_on_the_right_and_swapped_parts() -> None:
    doc = build_korpus()
    _add(doc, _plan(doc, front.FrontKind.DOOR, hinge_right=True))
    (hinge,) = doc.fittings.values()
    assert hinge.carrier_id == board_id(doc, "Seite_rechts")
    # door and side swapped: the parts lie the wrong way
    hinge.part_id, hinge.carrier_id = hinge.carrier_id, hinge.part_id
    assert _evaluate(doc).machining.errors[hinge.id] == JointErrorCode.ORIENTATION
    hinge.part_id, hinge.carrier_id = hinge.carrier_id, hinge.part_id
    doc.set_fitting_param(hinge.id, "cup_depth", 20)
    ev = _evaluate(doc)
    assert ev.machining.errors[hinge.id] == JointErrorCode.TOO_DEEP
    assert not ev.machining.hardware


@pytest.mark.parametrize("drawers", [1, 2])
def test_drawer_slides_on_both_sides(drawers: int) -> None:
    doc = build_korpus()
    ids, ev = _add(doc, _plan(doc, front.FrontKind.DRAWER, drawers=drawers))
    assert not ev.machining.errors
    assert {(f.kind, f.part_id) for f in doc.fittings.values()} == {
        (FittingKind.SLIDE, ids[2]),
        (FittingKind.SLIDE, ids[3]),
    }
    (item,) = ev.machining.hardware
    depth = ev.boards[ids[2]].size[1]
    assert item.length == fitting.slide_length(depth)
    assert item.quantity == 2 * drawers
    assert hardware_label(item) == f"Schubkastenschiene NL {item.length:g} mm"
    zarge = ev.boards[ids[2]]
    pilot = _holes(ev, ids[2], 2.5)
    assert sorted(round(h.center[1] - zarge.y) for h in pilot) == [37, 165, 293]
    assert all(h.center[0] == pytest.approx(zarge.x) for h in pilot)  # outer face
    side = _holes(ev, board_id(doc, "Seite_links"), 5)
    assert len(side) == 3 * drawers
    assert all(h.center[2] == pytest.approx(zarge.z + zarge.size[2] / 2) for h in side[:3])


def test_shelf_pin_row_leaves_out_holes_under_fixed_parts() -> None:
    doc = build_korpus()
    side = board_id(doc, "Seite_links")
    pins = doc.add_fitting(FittingKind.SHELF_PINS, side, None, 1)
    ev = _evaluate(doc)
    assert not ev.machining.errors
    assert not ev.machining.hardware
    holes = _holes(ev, side, 5)
    assert all(h.center[0] == pytest.approx(18) for h in holes)
    assert {round(h.center[1]) for h in holes} == {37, 363}
    heights = sorted({round(h.center[2], 3) for h in holes})
    assert heights[0] == pytest.approx(96)
    assert heights[-1] <= 1800 - 96
    shelves = ev.boards[board_id(doc, "Fachboden")]
    for origin in shelves.instance_origins():
        lo, hi = origin[2], origin[2] + shelves.thickness
        assert not any(lo - 2.5 < z < hi + 2.5 for z in heights)
    # on the outside nothing is in the way; a row with holes too deep is an error
    doc.fittings[pins.id].side = -1
    outside = _holes(_evaluate(doc), side, 5)
    assert len(outside) > len(holes)
    assert {h.direction for h in outside} == {1}
    doc.set_fitting_param(pins.id, "depth", 18)
    assert _evaluate(doc).machining.errors[pins.id] == JointErrorCode.TOO_DEEP
    with pytest.raises(ValueError):
        doc.add_fitting(FittingKind.SHELF_PINS, side, board_id(doc, "Boden"))


def test_fittings_go_with_their_boards(tmp_path: Path) -> None:
    doc = build_korpus()
    (door,), _ev = _add(doc, _plan(doc, front.FrontKind.DOOR))
    side = board_id(doc, "Seite_links")
    doc.add_fitting(FittingKind.SHELF_PINS, side, None, 1)
    path = tmp_path / "f.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert sdz.to_dict(loaded)["fittings"] == sdz.to_dict(doc)["fittings"]
    # an old file has none
    data = sdz.to_dict(doc)
    data["format_version"] = 4
    del data["fittings"]
    assert not sdz.from_dict(sdz.migrate(data)).fittings
    # copying door and side copies the hinge; deleting the side removes it
    copies = doc.duplicate([door, side])
    assert len(doc.fittings) == 4
    assert {f.part_id for f in doc.fittings.values()} >= set(copies)
    doc.delete([side], {})
    assert len(doc.fittings) == 2
    assert all(side not in f.board_ids() for f in doc.fittings.values())


def test_shelf_pin_tool_and_panel(qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        win.scene.snapping_enabled = False
        doc = build_korpus()
        doc.delete([board_id(doc, "Fachboden"), board_id(doc, "Rückwand")], {})
        win.controller._replace_document(doc, None)
        win.viewport.show_view("front")
        win.viewport.render()
        c = win.controller
        win.action_shelf_pins.trigger()
        tool = win.scene.tool
        assert isinstance(tool, ShelfPinTool)
        side = board_id(c.doc, "Seite_rechts")
        # the inner face of the right side, seen through the open front
        sx, sy = win.scene.to_display((782, 0, 900))
        hit = None
        for dx in range(0, 8):
            x, y = round(sx) - dx, round(sy)
            tool.hover(x, y)
            if tool.target is not None:
                hit = (x, y)
                break
        if hit is None:
            # the front view looks straight at the edges: use the panel instead
            c.add_fitting(FittingKind.SHELF_PINS, side, None, -1)
        else:
            assert tool.target == (side, -1)
            assert tool.holes > 0 and tool.preview.GetVisibility()
            assert tool.press(*hit, False) is True
        (pins,) = c.doc.fittings.values()
        assert (pins.part_id, pins.side) == (side, -1)
        panel = win.joints_panel
        panel.select_joint(pins.id)
        assert panel.current_id() == pins.id
        assert not panel.swap_button.isVisibleTo(panel)
        panel.fitting_spins["spacing"].setValue(64)
        assert c.doc.fittings[pins.id].param("spacing") == 64
        panel.delete_current()
        assert not c.doc.fittings
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
