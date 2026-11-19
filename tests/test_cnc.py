"""CNC export: one DXF per part with a layer per machining."""

from __future__ import annotations

from pathlib import Path

import pytest
from ezdxf.filemanagement import readfile

from simpledesign.core.io.cnc import cnc_parts, file_name, write_cnc
from simpledesign.core.materials.edgeband import BandEdge
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.joint import JointKind

from .furniture import board_id, build_korpus


def _korpus() -> Document:
    doc = build_korpus()
    for side in ("Seite_links", "Seite_rechts"):
        for part in ("Boden", "Deckel"):
            doc.add_joint(JointKind.DOWEL, board_id(doc, side), board_id(doc, part))
    return doc


def _layers(path: Path) -> dict[str, list[str]]:
    msp = readfile(path).modelspace()
    result: dict[str, list[str]] = {}
    for entity in msp:
        result.setdefault(entity.dxf.layer, []).append(entity.dxftype())
    return result


def test_dowels_become_holes_in_the_face_and_in_the_edge(tmp_path: Path) -> None:
    doc = _korpus()
    written = write_cnc(doc, Evaluator().evaluate(doc), tmp_path)
    names = {p.stem for p in written}
    assert {"Seite_links", "Seite_rechts", "Boden", "Deckel", "Rückwand"} <= names
    side = _layers(tmp_path / "Seite_links.dxf")
    face_holes = [layer for layer in side if layer.startswith(("BOHRUNG_D", "UNTEN_BOHRUNG_D"))]
    assert face_holes and all(set(side[layer]) == {"CIRCLE"} for layer in face_holes)
    assert len([e for layer in face_holes for e in side[layer]]) >= 4
    assert side["KONTUR"] == ["LWPOLYLINE"] and side["INFO"] == ["TEXT"]
    bottom = _layers(tmp_path / "Boden.dxf")
    assert any(layer.startswith("KANTE_BOHRUNG_D") for layer in bottom)


def test_contour_size_edge_banding_and_copies() -> None:
    doc = build_korpus()
    bottom = board_id(doc, "Boden")
    doc.set_edge_band(bottom, [BandEdge.L1, BandEdge.W2], "builtin.abs_1_white")
    ev = Evaluator().evaluate(doc)
    parts = {p.name: p for p in cnc_parts(doc, ev)}
    state = ev.boards[bottom]
    part = parts["Boden"]
    assert (part.length, part.width) == pytest.approx((state.length - 1, state.width - 1))
    xs = [x for x, _y in part.contour[0]]
    ys = [y for _x, y in part.contour[0]]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx(
        (0, state.length - 1, 0, state.width - 1)
    )
    shelf = board_id(doc, "Fachboden")
    copies = len(ev.machining.parts[shelf])
    assert copies > 1 and parts["Fachboden"].count == copies


def test_pockets_from_a_groove(tmp_path: Path) -> None:
    doc = build_korpus()
    doc.add_joint(JointKind.GROOVE, board_id(doc, "Seite_links"), board_id(doc, "Boden"))
    ev = Evaluator().evaluate(doc)
    side = next(p for p in cnc_parts(doc, ev) if p.name == "Seite_links")
    pockets = [layer for layer, _pts, _closed in side.polylines if "TASCHE_T" in layer]
    assert pockets, side.polylines


def test_file_names() -> None:
    assert file_name("Seite links/innen") == "Seite_links_innen"
    assert file_name("  ") == "Teil"


def test_dovetails_and_finger_joints_export(tmp_path: Path) -> None:
    doc = build_korpus()
    doc.add_joint(JointKind.DOVETAIL, board_id(doc, "Seite_links"), board_id(doc, "Deckel"))
    doc.add_joint(JointKind.FINGER, board_id(doc, "Seite_rechts"), board_id(doc, "Deckel"))
    ev = Evaluator().evaluate(doc)
    assert not ev.machining.errors
    written = write_cnc(doc, ev, tmp_path)
    layers = {layer for path in written for layer in _layers(path)}
    assert any("TASCHE" in layer or layer == "AUSSCHNITT" for layer in layers), layers
