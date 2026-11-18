from __future__ import annotations

import dataclasses
import itertools
import random

import pytest

from simpledesign.core.cutlist.optimize import (
    Piece,
    Placement,
    Sheet,
    optimize,
    pack_bars,
    pack_sheets,
)
from simpledesign.core.materials.library import builtin_material
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.settings import CutSettings
from tests.furniture import build_korpus, param_id


def overlaps(a: Placement, b: Placement, kerf: float) -> bool:
    return (
        a.x < b.x + b.length + kerf - 1e-6
        and b.x < a.x + a.length + kerf - 1e-6
        and a.y < b.y + b.width + kerf - 1e-6
        and b.y < a.y + a.width + kerf - 1e-6
    )


def assert_valid(sheet: Sheet, kerf: float) -> None:
    """Inside the trimmed sheet and at least one kerf apart."""
    for p in sheet.placements:
        assert p.x >= sheet.trim - 1e-6
        assert p.y >= sheet.trim - 1e-6
        assert p.x + p.length <= sheet.length - sheet.trim + 1e-6
        assert p.y + p.width <= sheet.width - sheet.trim + 1e-6
    for a, b in itertools.combinations(sheet.placements, 2):
        assert not overlaps(a, b, kerf), (a, b)


def test_four_quarters_fit_exactly_with_kerf() -> None:
    # 1000 x 500, kerf 4: two parts of 498 side by side need 498 + 4 + 498 = 1000.
    pieces = [Piece(f"P{i}", 498, 248) for i in range(4)]
    sheets, too_large = pack_sheets(pieces, 1000, 500, trim=0, kerf=4)
    assert len(sheets) == 1
    assert not too_large
    assert_valid(sheets[0], 4)


def test_one_more_mm_needs_second_sheet() -> None:
    pieces = [Piece(f"P{i}", 499, 248) for i in range(4)]
    sheets, _ = pack_sheets(pieces, 1000, 500, trim=0, kerf=4)
    assert len(sheets) == 2


def test_grain_prevents_rotation() -> None:
    sheets, _ = pack_sheets([Piece("A", 400, 450)] * 2, 1000, 500, trim=0, kerf=0)
    assert all(not p.rotated for s in sheets for p in s.placements)

    tall = Piece("B", 300, 900, has_grain=True)
    _, too_large = pack_sheets([tall], 1000, 500, trim=0, kerf=0)
    assert too_large == [tall]

    turned = Piece("C", 300, 900, has_grain=False)
    sheets, too_large = pack_sheets([turned], 1000, 500, trim=0, kerf=0)
    assert not too_large
    assert sheets[0].placements[0].rotated


def test_trim_reduces_usable_area() -> None:
    piece = Piece("A", 1000, 500)
    assert pack_sheets([piece], 1000, 500, trim=0, kerf=4)[1] == []
    assert pack_sheets([piece], 1000, 500, trim=10, kerf=4)[1] == [piece]


@pytest.mark.parametrize("seed", range(5))
def test_random_parts_never_overlap(seed: int) -> None:
    rng = random.Random(seed)
    pieces = [
        Piece(f"P{i}", rng.randint(50, 1200), rng.randint(50, 600), rng.random() < 0.5)
        for i in range(40)
    ]
    sheets, too_large = pack_sheets(pieces, 2500, 1250, trim=10, kerf=4)
    assert not too_large
    assert sum(len(s.placements) for s in sheets) == 40
    for sheet in sheets:
        assert_valid(sheet, 4)
    # Sanity bound: not much worse than the pure area bound.
    area = sum(p.length * p.width for p in pieces)
    assert len(sheets) <= area / (2480 * 1230) * 1.6 + 1


def test_bars_first_fit_decreasing() -> None:
    pieces = [Piece("A", 2000, 80), Piece("B", 2000, 80), Piece("C", 900, 80)]
    bars, too_large = pack_bars(pieces, 5000, kerf=4, allowance=10)
    assert not too_large
    assert len(bars) == 1
    assert [c.x for c in bars[0].cuts] == [0, 2014, 4028]  # 2000 + 10 allowance + 4 kerf


def test_bar_too_short() -> None:
    long = Piece("Lang", 4995, 80)
    bars, too_large = pack_bars([long], 5000, kerf=4, allowance=10)
    assert too_large == [long]
    assert not bars


def test_korpus_plan() -> None:
    doc = build_korpus()
    plans = {p.material.name: p for p in optimize(doc, Evaluator().evaluate(doc))}
    multiplex = plans["Multiplex Birke 18 mm"]
    assert len(multiplex.pieces) == 8  # 2 sides, bottom, top, 4 shelves
    assert multiplex.stock_count == 2
    expected = (2 * 1800 * 400 + 2 * 764 * 400 + 4 * 764 * 380) / 1e6
    assert multiplex.demand == pytest.approx(expected)
    assert plans["HDF 3 mm"].stock_count == 1


def test_plan_follows_parameters() -> None:
    doc = build_korpus()
    doc.set_expression(param_id(doc, "n"), None, "10")
    plans = {p.material.name: p for p in optimize(doc, Evaluator().evaluate(doc))}
    assert len(plans["Multiplex Birke 18 mm"].pieces) == 14


def test_cost_and_unplanned_material() -> None:
    doc = Document()
    board = doc.add_board("A")
    material = doc.materials[board.material_id]
    doc.update_material(dataclasses.replace(material, price=80.0))
    (plan,) = optimize(doc, Evaluator().evaluate(doc))
    assert plan.cost == 80.0

    doc.update_material(dataclasses.replace(material, stock_length=None))
    (plan,) = optimize(doc, Evaluator().evaluate(doc))
    assert not plan.planned
    assert plan.cost is None
    assert plan.stock_count == 0
    assert plan.demand == pytest.approx(0.6 * 0.3)


def test_timber_uses_bars_and_settings() -> None:
    doc = Document()
    doc.cut_settings = CutSettings(kerf=4, crosscut_allowance=20)
    kvh = builtin_material("builtin.kvh_60x80")
    for i in range(3):
        board = doc.add_board(f"Pfosten_{i + 1}", material_id=kvh)
        doc.set_expression(board.id, "length", "2400")
    (plan,) = optimize(doc, Evaluator().evaluate(doc))
    assert plan.material.is_linear
    assert plan.stock_count == 2  # 2 x (2400 + 20 + 4) fit into 5000
    assert plan.bars[0].cuts[1].x == 2424
    assert plan.demand == pytest.approx(7.2)
