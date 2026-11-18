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
BATTEN = "builtin.dachlatte_24x48"


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


def _carcass(
    doc: Document, g: str, back_in_groove: bool, shelves: bool = True
) -> tuple[Board, Board]:
    """Sides, bottom and top between the sides (dowelled), n shelves.
    Returns the two sides."""
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
    if not shelves:
        return left, right
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
    return left, right


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


def _fronts(doc: Document, g: str, name: str, along: Axis) -> Board:
    """Overlay fronts covering the carcass front: ``k`` drawers stacked (Z) or
    ``k`` doors side by side (X), ``f`` gap between them."""
    if along == Axis.Z:
        formulas = {"length": "B - f", "width": "(H - k*f) / k"}
        orientation = Orientation.XZ
    else:
        formulas = {"length": "H - f", "width": "(B - k*f) / k"}
        orientation = Orientation.ZX
    formulas |= {
        "x": "f/2",
        "y": f"-{name}.thickness",
        "z": "f/2",
        "count": "k",
        "spacing": f"{name}.width + f",
    }
    front = _board(doc, g, name, formulas, orientation)
    front.pattern_axis = along
    return front


def kommode() -> Template:
    doc = Document()
    params = {"H": "900", "B": "800", "T": "450", "t": "18", "k": "4", "f": "3"}
    g = _group(doc, "Kommode", params)
    _carcass(doc, g, back_in_groove=True, shelves=False)
    _fronts(doc, g, "Schubladenfront", Axis.Z)
    text = "Kommode: Korpus mit Rückwand in Nut, k aufschlagende Schubladenfronten (Fuge f)."
    return _template(doc, g, "Kommode", text)


def nachttisch() -> Template:
    doc = Document()
    params = {"H": "550", "B": "450", "T": "400", "t": "18", "h_s": "160", "f": "3"}
    g = _group(doc, "Nachttisch", params)
    sides = _carcass(doc, g, back_in_groove=True, shelves=False)
    divider = _board(
        doc,
        g,
        "Zwischenboden",
        {
            "length": "B - 2*t",
            "width": "Rückwand.y",
            "thickness": "t",
            "x": "Seite_links.x2",
            "z": "H - t - h_s",
        },
    )
    for side in sides:
        doc.add_joint(JointKind.DOWEL, side.id, divider.id)
    _board(
        doc,
        g,
        "Schubladenfront",
        {
            "length": "B - f",
            "width": "h_s + t - f",
            "x": "f/2",
            "y": "-Schubladenfront.thickness",
            "z": "Zwischenboden.z + f/2",
        },
        Orientation.XZ,
    )
    text = "Nachttisch: Korpus mit Schublade (Höhe h_s) oben und offenem Fach darunter."
    return _template(doc, g, "Nachttisch", text)


def haengeschrank() -> Template:
    doc = Document()
    params = {"H": "700", "B": "800", "T": "350", "t": "18", "n": "1", "k": "2", "f": "3"}
    g = _group(doc, "Hängeschrank", params)
    _carcass(doc, g, back_in_groove=True)
    _fronts(doc, g, "Tür", Axis.X)
    text = "Hängeschrank: Korpus mit Rückwand, n Fachböden, k aufschlagende Türen."
    return _template(doc, g, "Hängeschrank", text)


def sideboard() -> Template:
    doc = Document()
    params = {"H": "800", "B": "1500", "T": "420", "t": "18", "n": "1", "k": "3", "f": "3"}
    g = _group(doc, "Sideboard", params)
    _carcass(doc, g, back_in_groove=True)
    _fronts(doc, g, "Tür", Axis.X)
    text = "Sideboard / Schuhschrank: niedriger Korpus, n Fachböden, k Türen nebeneinander."
    return _template(doc, g, "Sideboard", text)


def schreibtisch() -> Template:
    doc = Document()
    g = _group(doc, "Schreibtisch", {"L": "1400", "T": "700", "H": "740", "h_k": "350"})
    top = _board(
        doc,
        g,
        "Platte",
        {"length": "L", "width": "T", "z": "H - Platte.thickness"},
        material=BEECH_27,
    )
    side = _board(
        doc,
        g,
        "Wange",
        {
            "length": "Platte.z",
            "width": "T - 50",
            "x": "30",
            "y": "25",
            "count": "2",
            "spacing": "L - 60 - Wange.thickness",
        },
        Orientation.ZY,
        BEECH_27,
    )
    side.pattern_axis = Axis.X
    panel = _board(
        doc,
        g,
        "Knieblende",
        {
            "length": "Wange.spacing - Wange.thickness",
            "width": "h_k",
            "x": "Wange.x2",
            "y": "T - 150",
            "z": "Platte.z - Knieblende.width",
        },
        Orientation.XZ,
        BEECH_18,
    )
    doc.add_joint(JointKind.DOWEL, top.id, side.id)
    doc.add_joint(JointKind.DOWEL, side.id, panel.id)
    text = "Schreibtisch: Platte auf zwei Wangen, Knieblende zur Aussteifung (gedübelt)."
    return _template(doc, g, "Schreibtisch", text)


def couchtisch() -> Template:
    doc = Document()
    g = _group(doc, "Couchtisch", {"L": "1000", "B": "600", "H": "420", "h_a": "120"})
    top = _board(
        doc,
        g,
        "Platte",
        {"length": "L", "width": "B", "z": "H - Platte.thickness"},
        material=BEECH_27,
    )
    side = _board(
        doc,
        g,
        "Wange",
        {"length": "Platte.z", "width": "B", "count": "2", "spacing": "L - Wange.thickness"},
        Orientation.ZY,
        BEECH_27,
    )
    side.pattern_axis = Axis.X
    shelf = _board(
        doc,
        g,
        "Ablage",
        {
            "length": "Wange.spacing - Wange.thickness",
            "width": "B - 40",
            "x": "Wange.x2",
            "y": "20",
            "z": "h_a",
        },
        material=BEECH_18,
    )
    doc.add_joint(JointKind.DOWEL, top.id, side.id)
    doc.add_joint(JointKind.DOWEL, side.id, shelf.id)
    text = "Couchtisch: Platte auf zwei Wangen, Ablage in Höhe h_a."
    return _template(doc, g, "Couchtisch", text)


def bett() -> Template:
    doc = Document()
    g = _group(doc, "Bett", {"L": "2000", "B": "1400", "H": "350", "n": "16"})
    head = _board(
        doc,
        g,
        "Kopfteil",
        {
            "length": "B",
            "width": "H",
            "count": "2",
            "spacing": "L + Kopfteil.thickness",
        },
        Orientation.YZ,
        BEECH_27,
    )
    head.pattern_axis = Axis.X
    side = _board(
        doc,
        g,
        "Seitenteil",
        {
            "length": "L + 2*Kopfteil.thickness",
            "width": "H",
            "count": "2",
            "spacing": "B + Seitenteil.thickness",
        },
        Orientation.XZ,
        BEECH_27,
    )
    side.pattern_axis = Axis.Y
    doc.set_expression(head.id, "y", "Seitenteil.thickness")
    ledger = _board(
        doc,
        g,
        "Auflageleiste",
        {
            "length": "L",
            "x": "Kopfteil.thickness",
            "y": "Seitenteil.thickness",
            "z": "H - 80 - Auflageleiste.width",
            "count": "2",
            "spacing": "B - Auflageleiste.thickness",
        },
        Orientation.XZ,
        BATTEN,
    )
    ledger.pattern_axis = Axis.Y
    slat = _board(
        doc,
        g,
        "Latte",
        {
            "length": "B - 2*Auflageleiste.thickness",
            "x": "Kopfteil.thickness + 20",
            "y": "Auflageleiste.y2",
            "z": "Auflageleiste.z2",
            "count": "n",
            "spacing": "(L - 40 - Latte.width) / (n - 1)",
        },
        Orientation.YX,
        BATTEN,
    )
    slat.pattern_axis = Axis.X
    text = "Bett (Liegefläche L x B): Kopf-/Fußteil, Seitenteile, Auflageleisten, n Latten."
    return _template(doc, g, "Bett", text)


def _template(doc: Document, group: str, name: str, description: str) -> Template:
    template = from_group(doc, group, {}, name, description)
    return Template(template.name, template.description, template.data, builtin=True)


BUILTINS: tuple[Callable[[], Template], ...] = (
    regal,
    korpus,
    kommode,
    nachttisch,
    haengeschrank,
    sideboard,
    tisch,
    schreibtisch,
    couchtisch,
    bank,
    bett,
)


@cache
def builtin_templates() -> tuple[Template, ...]:
    return tuple(make() for make in BUILTINS)
