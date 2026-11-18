from __future__ import annotations

import pytest

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.part import Axis, Orientation
from simpledesign.core.params.errors import ErrorCode, FormulaError
from tests.furniture import board_id, build_korpus, param_id


def evaluate(doc: Document, evaluator: Evaluator | None = None):  # type: ignore[no-untyped-def]
    return (evaluator or Evaluator()).evaluate(doc)


# ---------------------------------------------------------------- korpus regression


def test_korpus_dimensions() -> None:
    doc = build_korpus()
    ev = evaluate(doc)
    assert not ev.errors
    left = ev.boards[board_id(doc, "Seite_links")]
    assert left.size == (18, 400, 1800)
    right = ev.boards[board_id(doc, "Seite_rechts")]
    assert right.origin == (782, 0, 0)
    bottom = ev.boards[board_id(doc, "Boden")]
    assert bottom.size == (764, 400, 18)
    assert bottom.origin == (18, 0, 0)
    assert ev.boards[board_id(doc, "Deckel")].z == 1782
    back = ev.boards[board_id(doc, "Rückwand")]
    assert back.size == (800, 3, 1800)


def shelf_gaps(doc: Document) -> list[float]:
    """Free space between bottom, each shelf and top."""
    ev = evaluate(doc)
    shelf = ev.boards[board_id(doc, "Fachboden")]
    bottom_top = ev.boards[board_id(doc, "Boden")].z + 18
    top_z = ev.boards[board_id(doc, "Deckel")].z
    surfaces = [bottom_top]
    for origin in shelf.instance_origins():
        surfaces += [origin[2], origin[2] + shelf.thickness]
    surfaces.append(top_z)
    return [round(surfaces[i + 1] - surfaces[i], 6) for i in range(0, len(surfaces), 2)]


def test_shelves_evenly_spaced() -> None:
    doc = build_korpus()
    gaps = shelf_gaps(doc)
    assert len(gaps) == 5
    assert gaps == [pytest.approx(gaps[0])] * 5
    assert gaps[0] == pytest.approx((1800 - 36 - 72) / 5)


def test_change_height_updates_everything() -> None:
    doc = build_korpus()
    doc.set_expression(param_id(doc, "H"), None, "2000")
    ev = evaluate(doc)
    assert ev.boards[board_id(doc, "Seite_links")].length == 2000
    assert ev.boards[board_id(doc, "Deckel")].z == 1982
    gaps = shelf_gaps(doc)
    assert gaps == [pytest.approx((2000 - 36 - 72) / 5)] * 5


def test_change_shelf_count_updates_spacing() -> None:
    doc = build_korpus()
    doc.set_expression(param_id(doc, "n"), None, "2")
    gaps = shelf_gaps(doc)
    assert gaps == [pytest.approx((1800 - 36 - 36) / 3)] * 3


def test_material_copied_into_document() -> None:
    doc = build_korpus()
    assert set(doc.materials) == {"builtin.multiplex_birke_18", "builtin.hdf_3"}
    back = doc.boards[board_id(doc, "Rückwand")]
    assert back.expressions["thickness"] == "3"


# ---------------------------------------------------------------- names and display


def test_display_uses_names_and_storage_uses_ids() -> None:
    doc = build_korpus()
    right = board_id(doc, "Seite_rechts")
    stored = doc.stored_expression(right, "x")
    assert "Seite_rechts" not in stored
    assert stored.startswith("@")
    assert doc.display_expression(right, "x") == "B - Seite_rechts.thickness"


def test_rename_keeps_formulas_working() -> None:
    doc = build_korpus()
    doc.rename(param_id(doc, "t"), "Stärke")
    doc.rename(board_id(doc, "Boden"), "Unterboden")
    shelf = board_id(doc, "Fachboden")
    assert doc.display_expression(shelf, "z") == "Unterboden.z2 + Fachboden.spacing - Stärke"
    assert not evaluate(doc).errors


@pytest.mark.parametrize("name", ["1a", "a b", "a-b", "", "max", "a.b"])
def test_invalid_names(name: str) -> None:
    doc = Document()
    with pytest.raises(FormulaError) as info:
        doc.add_parameter(name, "1")
    assert info.value.code == ErrorCode.INVALID_NAME


def test_duplicate_names_rejected() -> None:
    doc = build_korpus()
    with pytest.raises(FormulaError) as info:
        doc.rename(board_id(doc, "Boden"), "Deckel")
    assert info.value.code == ErrorCode.DUPLICATE_NAME
    with pytest.raises(FormulaError):
        doc.add_parameter("H", "5")


def test_unknown_name_is_rejected_at_input() -> None:
    doc = build_korpus()
    with pytest.raises(FormulaError) as info:
        doc.set_expression(param_id(doc, "H"), None, "Breite * 2")
    assert info.value.code == ErrorCode.UNKNOWN_NAME
    with pytest.raises(FormulaError) as info:
        doc.set_expression(param_id(doc, "H"), None, "Boden.farbe")
    assert info.value.code == ErrorCode.UNKNOWN_PROPERTY


def test_decimal_comma_in_plain_number() -> None:
    doc = Document()
    p = doc.add_parameter("a", "18,5")
    assert p.expression == "18.5"


def test_unique_name() -> None:
    doc = build_korpus()
    assert doc.unique_name("Boden", is_parameter=False) == "Boden_2"
    assert doc.unique_name("Neu", is_parameter=False) == "Neu"


# ---------------------------------------------------------------- groups & scopes


def test_group_parameters_and_scopes() -> None:
    doc = Document()
    doc.add_parameter("t", "18")
    korpus = doc.add_group("Korpus")
    regal = doc.add_group("Regal")
    doc.add_parameter("H", "700", korpus.id)
    doc.add_parameter("H", "1200", regal.id)  # same name in sibling group: allowed
    side = doc.add_board("Seite", group_id=korpus.id)
    doc.set_expression(side.id, "length", "H - t")  # H = Korpus.H inside the group
    outside = doc.add_board("Außen")
    doc.set_expression(outside.id, "length", "Regal.H + Korpus.H")
    ev = evaluate(doc)
    assert ev.boards[side.id].length == 682
    assert ev.boards[outside.id].length == 1900
    assert doc.display_expression(side.id, "length") == "H - t"
    assert doc.display_expression(outside.id, "length") == "Regal.H + Korpus.H"
    with pytest.raises(FormulaError):
        doc.set_expression(outside.id, "length", "H")  # not visible at top level


def test_no_shadowing_between_scopes() -> None:
    doc = Document()
    doc.add_parameter("t", "18")
    group = doc.add_group("Korpus")
    with pytest.raises(FormulaError) as info:
        doc.add_parameter("t", "19", group.id)
    assert info.value.code == ErrorCode.DUPLICATE_NAME
    doc.add_parameter("d", "3", group.id)
    with pytest.raises(FormulaError):
        doc.add_parameter("d", "4")  # would be shadowed inside Korpus


def test_move_into_own_subgroup_rejected() -> None:
    doc = Document()
    outer = doc.add_group("A")
    inner = doc.add_group("B", outer.id)
    with pytest.raises(ValueError):
        doc.move_to_group([outer.id], inner.id)


def test_hidden_group_hides_boards() -> None:
    doc = Document()
    group = doc.add_group("G")
    board = doc.add_board(group_id=group.id)
    assert doc.is_visible(board)
    group.visible = False
    assert not doc.is_visible(board)


# ---------------------------------------------------------------- error behaviour


def test_cycle_keeps_last_valid_value() -> None:
    doc = Document()
    a = doc.add_parameter("a", "10")
    b = doc.add_parameter("b", "a * 2")
    evaluator = Evaluator()
    assert evaluator.evaluate(doc).value(b.id) == 20
    doc.set_expression(a.id, None, "b + 1")
    ev = evaluator.evaluate(doc)
    assert ev.error(a.id) is not None
    assert ev.error(a.id).code == ErrorCode.CYCLE  # type: ignore[union-attr]
    assert ev.value(a.id) == 10
    assert ev.value(b.id) == 20


def test_self_reference_is_cycle() -> None:
    doc = Document()
    board = doc.add_board("Brett")
    doc.set_expression(board.id, "length", "Brett.length + 1")
    ev = evaluate(doc)
    assert ev.error(board.id, "length").code == ErrorCode.CYCLE
    assert ev.boards[board.id].length == 600  # default fallback


def test_non_positive_dimension_keeps_last_valid() -> None:
    doc = Document()
    t = doc.add_parameter("t", "18")
    board = doc.add_board()
    doc.set_expression(board.id, "thickness", "t - 10")
    evaluator = Evaluator()
    assert evaluator.evaluate(doc).boards[board.id].thickness == 8
    doc.set_expression(t.id, None, "5")
    ev = evaluator.evaluate(doc)
    assert ev.error(board.id, "thickness").code == ErrorCode.NOT_POSITIVE  # type: ignore[union-attr]
    assert ev.boards[board.id].thickness == 8


def test_count_must_be_integer() -> None:
    doc = Document()
    board = doc.add_board()
    doc.set_expression(board.id, "count", "7 / 2")
    ev = evaluate(doc)
    assert ev.error(board.id, "count").code == ErrorCode.NOT_INTEGER
    doc.set_expression(board.id, "count", "floor(7 / 2)")
    assert evaluate(doc).boards[board.id].count == 3


def test_division_by_zero_reported() -> None:
    doc = Document()
    p = doc.add_parameter("p", "1 / 0")
    ev = evaluate(doc)
    assert ev.error(p.id).code == ErrorCode.DIVISION_BY_ZERO


# ---------------------------------------------------------------- delete & duplicate


def test_delete_freezes_dependent_formulas() -> None:
    doc = build_korpus()
    ev = evaluate(doc)
    bottom = board_id(doc, "Boden")
    shelf = board_id(doc, "Fachboden")
    assert (shelf, "z") in doc.external_references([bottom])
    doc.delete([bottom], ev.values)
    assert doc.display_expression(shelf, "z") == "18 + Fachboden.spacing - t"
    after = evaluate(doc)
    assert not after.errors
    assert after.boards[shelf].z == ev.boards[shelf].z


def test_delete_group_removes_contents() -> None:
    doc = Document()
    group = doc.add_group("G")
    sub = doc.add_group("S", group.id)
    doc.add_board("A", group_id=sub.id)
    doc.add_parameter("p", "1", sub.id)
    doc.delete([group.id], {})
    assert not doc.boards
    assert not doc.groups
    assert not doc.parameters
    assert not doc.materials


def test_duplicate_group_remaps_internal_references() -> None:
    doc = Document()
    group = doc.add_group("Korpus")
    doc.add_parameter("H", "700", group.id)
    side = doc.add_board("Seite", group_id=group.id)
    doc.set_expression(side.id, "length", "H")
    doc.set_expression(side.id, "z", "Seite.thickness")
    (copy_id,) = doc.duplicate([group.id])
    copy_group = doc.groups[copy_id]
    assert copy_group.name == "Korpus_2"
    copied_side = next(b for b in doc.boards.values() if b.group_id == copy_id)
    assert copied_side.name == "Seite_2"
    assert doc.display_expression(copied_side.id, "z") == "Seite_2.thickness"
    copied_h = next(p for p in doc.parameters.values() if p.group_id == copy_id)
    doc.set_expression(copied_h.id, None, "900")
    ev = evaluate(doc)
    assert ev.boards[side.id].length == 700
    assert ev.boards[copied_side.id].length == 900


# ---------------------------------------------------------------- orientation & pattern


def test_orientation_axes() -> None:
    assert Orientation.ZY.thickness_axis == Axis.X
    assert Orientation.XZ.thickness_axis == Axis.Y
    assert Orientation.XY.dimension_along(Axis.Z) == "thickness"


def test_pattern_instances() -> None:
    doc = Document()
    board = doc.add_board()
    doc.set_expression(board.id, "count", "3")
    doc.set_expression(board.id, "spacing", "100")
    doc.boards[board.id].pattern_axis = Axis.X
    origins = evaluate(doc).boards[board.id].instance_origins()
    assert origins == [(0, 0, 0), (100, 0, 0), (200, 0, 0)]
