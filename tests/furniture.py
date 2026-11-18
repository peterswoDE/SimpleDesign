"""Example furniture used as regression fixtures."""

from __future__ import annotations

from simpledesign.core.model.document import Document
from simpledesign.core.model.part import Orientation


def build_korpus() -> Document:
    """A cabinet carcass: 2 sides, bottom, top, n shelves evenly spaced, back panel."""
    doc = Document()
    doc.add_parameter("H", "1800")
    doc.add_parameter("B", "800")
    doc.add_parameter("T", "400")
    doc.add_parameter("t", "18")
    doc.add_parameter("n", "4")

    left = doc.add_board("Seite_links", orientation=Orientation.ZY)
    for prop, text in {"length": "H", "width": "T", "thickness": "t"}.items():
        doc.set_expression(left.id, prop, text)

    right = doc.duplicate([left.id])[0]
    doc.rename(right, "Seite_rechts")
    doc.set_expression(right, "x", "B - Seite_rechts.thickness")

    bottom = doc.add_board("Boden")
    for prop, text in {
        "length": "B - 2*t",
        "width": "T",
        "thickness": "t",
        "x": "Seite_links.x2",
    }.items():
        doc.set_expression(bottom.id, prop, text)

    top = doc.duplicate([bottom.id])[0]
    doc.rename(top, "Deckel")
    doc.set_expression(top, "z", "H - t")

    shelf = doc.add_board("Fachboden")
    for prop, text in {
        "length": "B - 2*t",
        "width": "T - 20",
        "thickness": "t",
        "x": "t",
        "count": "n",
        # evenly distributed: n shelves, n + 1 equal gaps between bottom and top
        "spacing": "(H - 2*t - n*t) / (n + 1) + t",
        "z": "Boden.z2 + Fachboden.spacing - t",
    }.items():
        doc.set_expression(shelf.id, prop, text)

    back = doc.add_board("Rückwand", material_id="builtin.hdf_3", orientation=Orientation.XZ)
    for prop, text in {"length": "B", "width": "H", "y": "T"}.items():
        doc.set_expression(back.id, prop, text)
    return doc


def board_id(doc: Document, name: str) -> str:
    obj = doc.board_or_group_named(name)
    assert obj is not None
    return obj.id


def param_id(doc: Document, name: str) -> str:
    param = doc.lookup_parameter(name, None)
    assert param is not None
    return param.id
