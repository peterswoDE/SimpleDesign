"""Grain groups (Maserung über Fronten): parts cut from one piece."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.cutlist.optimize import (
    Piece,
    _Laid,
    grain_block,
    optimize,
    pack_sheets,
    single_pieces,
)
from simpledesign.core.io import sdz
from simpledesign.core.model import front
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator

from .furniture import board_id, build_korpus
from .test_front import _plan


def test_block_layout() -> None:
    a, b, c = Piece("A", 400, 150), Piece("B", 400, 150), Piece("C", 300, 100)
    # A and B lie next to each other across the grain, C follows A along it
    block = grain_block("G", [_Laid(b, 0, 154), _Laid(a, 0, 0), _Laid(c, 404, 0)], kerf=4)
    assert block.length == pytest.approx(704) and block.width == pytest.approx(304)
    assert [(m.name, x, y) for m, x, y in block.members] == [
        ("A", 0, 0),
        ("C", 404, 0),
        ("B", 0, 154),
    ]
    assert [p.name for p in single_pieces([block, Piece("D", 1, 1)])] == ["A", "C", "B", "D"]


def test_block_packed_in_one_piece_or_split_when_too_large() -> None:
    parts = [_Laid(Piece(n, 500, 200), 0, k * 204) for k, n in enumerate("ABC")]
    block = grain_block("G", parts, kerf=4)
    sheets, too_large = pack_sheets([block, Piece("X", 300, 300)], 2800, 2070, 4, 10)
    assert not too_large and len(sheets) == 1
    (outline,) = sheets[0].blocks
    by_name = {p.name: p for p in sheets[0].placements}
    assert [by_name[n].y - outline.y for n in "ABC"] == [0, 204, 408]
    assert all(by_name[n].x == outline.x and not by_name[n].rotated for n in "ABC")
    # a sheet too small for the block: the parts are packed one by one
    sheets, too_large = pack_sheets([block], 520, 500, 4, 0)
    assert not too_large and not any(s.blocks for s in sheets)
    assert sorted(p.name for s in sheets for p in s.placements) == ["A", "B", "C"]


def _drawers(doc: Document) -> str:
    plan = _plan(doc, front.FrontKind.DRAWER, drawers=3)
    ids = front.create(doc, plan, None)
    return ids[0]


def test_drawer_fronts_share_the_grain() -> None:
    doc = build_korpus()
    front_id = _drawers(doc)
    assert doc.boards[front_id].grain_group == "Maserung_1"
    plans = optimize(doc, Evaluator().evaluate(doc))
    blocks = [b for plan in plans for s in plan.sheets for b in s.blocks]
    assert [b.name for b in blocks] == ["Maserung_1"]
    name = doc.boards[front_id].name
    inside = [
        p
        for plan in plans
        for s in plan.sheets
        for p in s.placements
        if p.name == name
        and blocks[0].x <= p.x < blocks[0].x + blocks[0].length
        and blocks[0].y <= p.y < blocks[0].y + blocks[0].width
    ]
    assert len(inside) == 3
    # the net demand counts the parts, not the block
    total = sum(plan.demand for plan in plans)
    doc.boards[front_id].grain_group = None
    assert sum(plan.demand for plan in optimize(doc, Evaluator().evaluate(doc))) == (
        pytest.approx(total)
    )


def test_controller_and_file(tmp_path: Path) -> None:
    c = Controller()
    c._replace_document(build_korpus(), None)
    ids = [board_id(c.doc, "Boden"), board_id(c.doc, "Deckel")]
    assert c.set_grain_group(ids, True) == "Maserung_1"
    assert {c.doc.boards[i].grain_group for i in ids} == {"Maserung_1"}
    path = tmp_path / "g.sdz"
    sdz.save(c.doc, path)
    assert sdz.load(path).boards[ids[0]].grain_group == "Maserung_1"
    assert c.set_grain_group(ids[:1], True) == "Maserung_2"
    c.set_grain_group(ids, False)
    assert all(c.doc.boards[i].grain_group is None for i in ids)
    c.undo_stack.undo()
    assert c.doc.boards[ids[1]].grain_group == "Maserung_1"


def test_grain_actions_in_the_window(qtbot: QtBot) -> None:
    from simpledesign.ui.main_window import MainWindow

    win = MainWindow()
    try:
        c = win.controller
        c._replace_document(build_korpus(), None)
        ids = [board_id(c.doc, "Boden"), board_id(c.doc, "Deckel")]
        c.set_selection(ids[:1])
        assert not win.action_grain_join.isEnabled() and win.action_grain_split.isEnabled()
        c.set_selection(ids)
        win.action_grain_join.trigger()
        assert {c.doc.boards[i].grain_group for i in ids} == {"Maserung_1"}
        c.set_selection(ids[:1])
        assert win.properties_panel.grain_group_label.text() == "Maserung_1"
        win.action_grain_split.trigger()
        assert c.doc.boards[ids[0]].grain_group is None
        assert win.properties_panel.grain_group_label.text() == "–"
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()
