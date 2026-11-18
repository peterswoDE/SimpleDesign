"""Built-in templates, built with the document API (so they stay valid).

Every template is one group whose parameters are the main dimensions. The
parts reference the parameters and each other, so the furniture keeps its
construction when a dimension changes.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from simpledesign.core.model.document import Document
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.part import Axis, Board, Orientation
from simpledesign.core.templates.template import Template, from_group

HDF = "builtin.hdf_3"
BEECH_27 = "builtin.leimholz_buche_27"
BEECH_18 = "builtin.leimholz_buche_18"
TIMBER = "builtin.kvh_60x80"


def _board(
    doc: Document,
    group: str,
    name: str,
    formulas: dict[str, str],
    orientation: Orientation = Orientation.XY,
    material: str | None = None,
) -> Board:
    if material is None:
        board = doc.add_board(name, group, orientation=orientation)
    else:
        board = doc.add_board(name, group, material, orientation)
    for prop, text in formulas.items():
        doc.set_expression(board.id, prop, text)
    return board


def _group(doc: Document, name: str, params: dict[str, str]) -> str:
    group = doc.add_group(name).id
    for param, text in params.items():
        doc.add_parameter(param, text, group)
    return group


def _carcass(doc: Document, g: str, back_in_groove: bool) -> None:
    """Sides, bottom and top between the sides (dowelled), n shelves."""
    upright = {"length": "H", "width": "T", "thickness": "t"}
    left = _board(doc, g, "Seite_links", upright, Orientation.ZY)
    right = _board(doc, g, "Seite_rechts", {**upright, "x": "B - t"}, Orientation.ZY)
    inner = {"length": "B - 2*t", "width": "T", "thickness": "t", "x": "Seite_links.x2"}
    bottom = _board(doc, g, "Boden", inner)
    top = _board(doc, g, "Deckel", {**inner, "z": "H - t"})
    for side in (left, right):
        for part in (bottom, top):
            doc.add_joint(JointKind.DOWEL, side.id, part.id)
    shelf_depth = "T - 2"
    if back_in_groove:
        back = _board(
            doc,
            g,
            "Rückwand",
            {
                "length": "B - 2*t",
                "width": "H - 2*t",
                "x": "Seite_links.x2",
                "y": "T - 10 - Rückwand.thickness",
                "z": "Boden.z2",
            },
            Orientation.XZ,
            HDF,
        )
        for main in (left, right, bottom, top):
            joint = doc.add_joint(JointKind.GROOVE, main.id, back.id)
            doc.set_joint_param(joint.id, "depth", 8)
        shelf_depth = "Rückwand.y - 2"
    _board(
        doc,
        g,
        "Fachboden",
        {
            "length": "B - 2*t - 2",
            "width": shelf_depth,
            "thickness": "t",
            "x": "Seite_links.x2 + 1",
            "count": "n",
            # n shelves, n + 1 equal gaps between bottom and top
            "spacing": "(H - 2*t - n*t) / (n + 1) + t",
            "z": "Boden.z2 + Fachboden.spacing - t",
        },
    )


def regal() -> Template:
    doc = Document()
    g = _group(doc, "Regal", {"H": "1800", "B": "800", "T": "300", "t": "18", "n": "4"})
    _carcass(doc, g, back_in_groove=False)
    text = "Offenes Regal: Seiten, Boden, Deckel (gedübelt), n gleichmäßig verteilte Fachböden."
    return _template(doc, g, "Regal", text)


def korpus() -> Template:
    doc = Document()
    g = _group(doc, "Schrank", {"H": "2000", "B": "1000", "T": "580", "t": "18", "n": "3"})
    _carcass(doc, g, back_in_groove=True)
    text = "Schrankkorpus: Seiten, Boden, Deckel (gedübelt), Rückwand in Nut, n Fachböden."
    return _template(doc, g, "Korpus / Schrank", text)


def tisch() -> Template:
    doc = Document()
    g = _group(doc, "Tisch", {"L": "1600", "B": "800", "H": "750", "u": "60", "h_z": "100"})
    _board(
        doc,
        g,
        "Tischplatte",
        {"length": "L", "width": "B", "z": "H - Tischplatte.thickness"},
        material=BEECH_27,
    )
    legs: list[Board] = []
    for name, y in (("Bein_vorne", "u"), ("Bein_hinten", "B - u - Bein_hinten.thickness")):
        leg = _board(
            doc,
            g,
            name,
            {
                "length": "Tischplatte.z",
                "x": "u",
                "y": y,
                "count": "2",
                "spacing": f"L - 2*u - {name}.width",
            },
            Orientation.ZX,
            TIMBER,
        )
        leg.pattern_axis = Axis.X
        legs.append(leg)
    front, back = legs
    pairs: list[tuple[Board, Board]] = []
    for name, leg in (("Zarge_vorne", front), ("Zarge_hinten", back)):
        apron = _board(
            doc,
            g,
            name,
            {
                "length": f"{leg.name}.spacing - {leg.name}.width",
                "width": "h_z",
                "x": f"{leg.name}.x2",
                "y": f"{leg.name}.y + ({leg.name}.thickness - {name}.thickness) / 2",
                "z": f"Tischplatte.z - {name}.width",
            },
            Orientation.XZ,
            BEECH_18,
        )
        pairs.append((leg, apron))
    side = _board(
        doc,
        g,
        "Zarge_seitlich",
        {
            "length": "Bein_hinten.y - Bein_vorne.y2",
            "width": "h_z",
            "x": "Bein_vorne.x + (Bein_vorne.width - Zarge_seitlich.thickness) / 2",
            "y": "Bein_vorne.y2",
            "z": "Tischplatte.z - Zarge_seitlich.width",
            "count": "2",
            "spacing": "Bein_vorne.spacing",
        },
        Orientation.YZ,
        BEECH_18,
    )
    side.pattern_axis = Axis.X
    pairs += [(front, side), (back, side)]
    for leg, apron in pairs:
        joint = doc.add_joint(JointKind.TENON, leg.id, apron.id)
        doc.set_joint_param(joint.id, "length", 25)
    text = "Tisch mit vier Beinen (KVH) und Zarge, Zarge mit Zapfen in den Beinen."
    return _template(doc, g, "Tisch", text)


def bank() -> Template:
    doc = Document()
    g = _group(doc, "Bank", {"L": "1200", "T": "350", "H": "450", "a": "120"})
    seat = _board(
        doc,
        g,
        "Sitzfläche",
        {"length": "L", "width": "T", "z": "H - Sitzfläche.thickness"},
        material=BEECH_27,
    )
    leg = _board(
        doc,
        g,
        "Wange",
        {
            "length": "Sitzfläche.z",
            "width": "T - 40",
            "x": "a",
            "y": "20",
            "count": "2",
            "spacing": "L - 2*a - Wange.thickness",
        },
        Orientation.ZY,
        BEECH_27,
    )
    leg.pattern_axis = Axis.X
    rail = _board(
        doc,
        g,
        "Traverse",
        {
            "length": "Wange.spacing - Wange.thickness",
            "width": "100",
            "x": "Wange.x2",
            "y": "(T - Traverse.thickness) / 2",
            "z": "150",
        },
        Orientation.XZ,
        BEECH_27,
    )
    doc.add_joint(JointKind.DOWEL, seat.id, leg.id)
    joint = doc.add_joint(JointKind.TENON, leg.id, rail.id)
    doc.set_joint_param(joint.id, "length", 20)
    text = "Sitzbank: Sitzfläche auf zwei Wangen (gedübelt), Traverse mit Zapfen."
    return _template(doc, g, "Bank", text)


def _template(doc: Document, group: str, name: str, description: str) -> Template:
    template = from_group(doc, group, {}, name, description)
    return Template(template.name, template.description, template.data, builtin=True)


BUILTINS: tuple[Callable[[], Template], ...] = (regal, korpus, tisch, bank)


@cache
def builtin_templates() -> tuple[Template, ...]:
    return tuple(make() for make in BUILTINS)
