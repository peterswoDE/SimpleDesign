from __future__ import annotations

from simpledesign.core.cutlist.cutlist import build_cutlist, to_csv
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.part import Orientation
from tests.furniture import build_korpus, param_id

HEADERS = (
    "Anzahl",
    "Länge",
    "Breite",
    "Dicke",
    "Material",
    "Bearbeitung",
    "Kanten",
    "Teile",
)


def summary(doc: Document) -> list[tuple[int, float, float, float, str]]:
    rows = build_cutlist(doc, Evaluator().evaluate(doc))
    return [(r.quantity, r.length, r.width, r.thickness, r.material) for r in rows]


def test_korpus_cutlist() -> None:
    assert summary(build_korpus()) == [
        (1, 1800, 800, 3, "HDF 3 mm"),  # no grain: normalised to length >= width
        (2, 1800, 400, 18, "Multiplex Birke 18 mm"),
        (2, 764, 400, 18, "Multiplex Birke 18 mm"),
        (4, 764, 380, 18, "Multiplex Birke 18 mm"),
    ]


def test_cutlist_follows_parameters() -> None:
    doc = build_korpus()
    doc.set_expression(param_id(doc, "n"), None, "6")
    doc.set_expression(param_id(doc, "H"), None, "2000")
    rows = summary(doc)
    assert (6, 764, 380, 18, "Multiplex Birke 18 mm") in rows
    assert (2, 2000, 400, 18, "Multiplex Birke 18 mm") in rows


def test_grain_keeps_length_and_width_apart() -> None:
    doc = Document()
    a = doc.add_board("A")
    b = doc.add_board("B", orientation=Orientation.YX)
    doc.set_expression(b.id, "length", "300")
    doc.set_expression(b.id, "width", "600")
    doc.set_expression(a.id, "width", "300")  # A: 600 x 300
    assert len(summary(doc)) == 2  # grain differs


def test_no_grain_merges_rotated_parts() -> None:
    doc = Document()
    a = doc.add_board("A", material_id="builtin.mdf_19")
    b = doc.add_board("B", material_id="builtin.mdf_19")
    doc.set_expression(b.id, "length", "300")
    doc.set_expression(b.id, "width", "600")
    doc.set_expression(a.id, "width", "300")
    assert summary(doc) == [(2, 600, 300, 19, "MDF 19 mm")]


def test_csv_uses_semicolon_and_decimal_comma() -> None:
    doc = Document()
    board = doc.add_board("Brett")
    doc.set_expression(board.id, "length", "600.5")
    rows = build_cutlist(doc, Evaluator().evaluate(doc))
    text = to_csv(rows, HEADERS, lambda row: f"{row.holes} B", lambda row: "")
    lines = text.splitlines()
    assert lines[0] == "Anzahl;Länge;Breite;Dicke;Material;Bearbeitung;Kanten;Teile"
    assert lines[1] == "1;600,5;300;18;Multiplex Birke 18 mm;0 B;;Brett"
