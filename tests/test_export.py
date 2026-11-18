from __future__ import annotations

import struct
from pathlib import Path

import pytest

from simpledesign.core.io.export import ExportError, export, export_bodies
from simpledesign.core.model.evaluate import Evaluator
from tests.furniture import board_id, build_korpus


def korpus_export(tmp_path: Path, name: str) -> Path:
    doc = build_korpus()
    path = tmp_path / name
    export(doc, Evaluator().evaluate(doc), path)
    return path


def test_bodies_include_pattern_copies_but_not_hidden() -> None:
    doc = build_korpus()
    doc.boards[board_id(doc, "Rückwand")].visible = False
    names = [b.name for b in export_bodies(doc, Evaluator().evaluate(doc))]
    assert "Fachboden_4" in names
    assert "Rückwand" not in names
    assert len(names) == 8


def test_step_keeps_names_and_millimetres(tmp_path: Path) -> None:
    text = korpus_export(tmp_path, "korpus.step").read_text(encoding="latin-1")
    assert "PRODUCT('Seite_links'" in text
    assert text.count("MANIFOLD_SOLID_BREP") == 9
    assert "SI_UNIT(.MILLI.,.METRE.)" in text


def test_stl_triangle_count_and_bounds(tmp_path: Path) -> None:
    data = korpus_export(tmp_path, "korpus.stl").read_bytes()
    (count,) = struct.unpack_from("<I", data, 80)
    assert count == 9 * 12
    assert len(data) == 84 + count * 50
    # Each record: normal (12 bytes), 3 vertices (36 bytes), attribute (2 bytes).
    xs = [
        struct.unpack_from("<3f", data, 84 + i * 50 + 12 + k * 12)[0]
        for i in range(count)
        for k in range(3)
    ]
    assert min(xs) == pytest.approx(0)
    assert max(xs) == pytest.approx(800)


def test_obj_has_named_objects(tmp_path: Path) -> None:
    lines = korpus_export(tmp_path, "korpus.obj").read_text(encoding="utf-8").splitlines()
    assert "o Seite_links" in lines
    faces = [line for line in lines if line.startswith("f ")]
    assert len(faces) == 9 * 12
    vertex_count = sum(line.startswith("v ") for line in lines)
    assert max(int(i) for line in faces for i in line.split()[1:]) == vertex_count


def test_unknown_suffix(tmp_path: Path) -> None:
    with pytest.raises(ExportError):
        korpus_export(tmp_path, "korpus.dwg")
