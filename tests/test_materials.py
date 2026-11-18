from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from simpledesign.core.cutlist.cutlist import build_cutlist
from simpledesign.core.materials.library import (
    BUILTIN_MATERIALS,
    DEFAULT_MATERIAL_ID,
    LibraryError,
    MaterialLibrary,
    builtin_material,
)
from simpledesign.core.materials.material import Material, MaterialKind
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator


def test_missing_file_gives_builtins(tmp_path: Path) -> None:
    library = MaterialLibrary.load(tmp_path / "materials.json")
    assert list(library) == list(BUILTIN_MATERIALS)
    assert library.default().id == DEFAULT_MATERIAL_ID


def test_roundtrip_with_own_material(tmp_path: Path) -> None:
    own = Material(
        "own.1",
        "Kiefer Bohle 40",
        MaterialKind.SOLID,
        40,
        stock_length=3000,
        stock_width=200,
        edge_trim=15,
        price=42.5,
    )
    library = MaterialLibrary([*BUILTIN_MATERIALS, own])
    path = tmp_path / "materials.json"
    library.save(path)
    assert MaterialLibrary.load(path) == library


@pytest.mark.parametrize("content", ["kein json", "[]", '{"format_version": 1}'])
def test_invalid_library_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "materials.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(LibraryError):
        MaterialLibrary.load(path)


def test_document_keeps_own_copy() -> None:
    doc = Document()
    library_version = builtin_material(DEFAULT_MATERIAL_ID)
    board = doc.add_board("A", material_id=library_version)
    changed = dataclasses.replace(library_version, thickness=19, price=99)
    # Assigning the same ID again keeps the document's copy ...
    doc.set_material(board.id, changed)
    assert doc.materials[DEFAULT_MATERIAL_ID].thickness == 18
    # ... until it is updated explicitly; a plain-number thickness follows.
    doc.update_material(changed)
    assert doc.materials[DEFAULT_MATERIAL_ID].price == 99
    assert doc.boards[board.id].expressions["thickness"] == "19"


def test_timber_sets_cross_section() -> None:
    doc = Document()
    board = doc.add_board("Pfosten", material_id="builtin.kvh_60x120")
    assert board.expressions["thickness"] == "60"
    assert board.expressions["width"] == "120"


def test_ignore_grain_merges_rotated_parts() -> None:
    doc = Document()
    a = doc.add_board("A")
    b = doc.add_board("B")
    doc.set_expression(a.id, "width", "300")
    doc.set_expression(b.id, "length", "300")
    doc.set_expression(b.id, "width", "600")
    ev = Evaluator().evaluate(doc)
    assert len(build_cutlist(doc, ev)) == 2
    a.ignore_grain = True
    b.ignore_grain = True
    (row,) = build_cutlist(doc, ev)
    assert (row.quantity, row.length, row.width, row.has_grain) == (2, 600, 300, False)
