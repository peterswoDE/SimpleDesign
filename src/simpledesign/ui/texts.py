"""Translatable texts and number display shared by several widgets."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale

from simpledesign.core.materials.edgeband import BandEdge
from simpledesign.core.materials.material import MaterialKind
from simpledesign.core.model.fitting import FittingKind
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.machining import Hardware, JointErrorCode
from simpledesign.core.model.part import Axis, Orientation, band_edge_side
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import format_number


def _tr(text: str) -> str:
    return QCoreApplication.translate("Texts", text)


def profile_problem(problem: str) -> str:
    return {
        "empty": _tr("keine Geometrie"),
        "open": _tr("Kontur offen"),
        "branched": _tr("Linien verzweigen sich"),
    }.get(problem, problem)


def error_message(error: FormulaError) -> str:
    p = error.params
    messages = {
        ErrorCode.EMPTY: _tr("Die Formel ist leer."),
        ErrorCode.SYNTAX: _tr("Die Formel ist ungültig (Stelle {position})."),
        ErrorCode.DECIMAL_COMMA: _tr(
            "In Formeln ist der Punkt das Dezimalzeichen (18.5); Argumente trennt ';'."
        ),
        ErrorCode.UNKNOWN_NAME: _tr("Unbekannter Name: {name}"),
        ErrorCode.UNKNOWN_PROPERTY: _tr("Unbekannte Eigenschaft: {name}"),
        ErrorCode.UNKNOWN_FUNCTION: _tr("Unbekannte Funktion: {name}"),
        ErrorCode.ARGUMENT_COUNT: _tr("Falsche Anzahl Argumente für {name}."),
        ErrorCode.DIVISION_BY_ZERO: _tr("Division durch null."),
        ErrorCode.NOT_FINITE: _tr("Das Ergebnis ist keine endliche Zahl."),
        ErrorCode.CYCLE: _tr("Zirkelbezug: die Formel hängt von sich selbst ab."),
        ErrorCode.NOT_POSITIVE: _tr("Der Wert muss größer als 0 sein (ist {value})."),
        ErrorCode.NOT_INTEGER: _tr("Die Anzahl muss eine ganze Zahl sein (ist {value})."),
        ErrorCode.INVALID_NAME: _tr(
            "Ungültiger Name „{name}“: nur Buchstaben, Ziffern und _, nicht mit Ziffer beginnen."
        ),
        ErrorCode.DUPLICATE_NAME: _tr("Der Name „{name}“ ist schon vergeben."),
        ErrorCode.SKETCH_UNSOLVABLE: _tr(
            "Die Skizze ist widersprüchlich – Bedingungen passen nicht zusammen."
        ),
        ErrorCode.PROFILE_INVALID: _tr("Die Skizze ergibt keine geschlossene Kontur ({problem})."),
    }
    if error.code == ErrorCode.PROFILE_INVALID:
        p = {"problem": profile_problem(str(p.get("problem", "")))}
    values = {key: number(v) if isinstance(v, float) else v for key, v in p.items()}
    try:
        return messages[error.code].format(**values)
    except KeyError:
        return messages[error.code]


def number(value: float) -> str:
    """A value for display, with the decimal separator of the UI language."""
    return format_number(value).replace(".", QLocale().decimalPoint())


def axis_label(axis: Axis) -> str:
    return axis.value.upper()


def orientation_label(orientation: Orientation) -> str:
    return _tr("Länge {l} · Breite {w} · Dicke {t}").format(
        l=axis_label(orientation.length_axis),
        w=axis_label(orientation.width_axis),
        t=axis_label(orientation.thickness_axis),
    )


def property_label(prop: str) -> str:
    labels = {
        "length": _tr("Länge"),
        "width": _tr("Breite"),
        "thickness": _tr("Dicke"),
        "x": "X",
        "y": "Y",
        "z": "Z",
        "count": _tr("Anzahl"),
        "spacing": _tr("Abstand"),
        "angle": _tr("Winkel"),
    }
    return labels[prop]


def material_kind_label(kind: MaterialKind) -> str:
    labels = {
        MaterialKind.PANEL: _tr("Plattenware"),
        MaterialKind.SOLID: _tr("Massivholz (Bretter/Bohlen)"),
        MaterialKind.TIMBER: _tr("Kantholz/Latten (Stangenware)"),
    }
    return labels[kind]


def percent(value: float) -> str:
    return QLocale().toString(value * 100, "f", 0) + " %"


def money(value: float) -> str:
    return QLocale().toString(value, "f", 2) + " €"


def joint_kind_label(kind: JointKind) -> str:
    labels = {
        JointKind.DOWEL: _tr("Dübel"),
        JointKind.SCREW: _tr("Schrauben"),
        JointKind.GROOVE: _tr("Nut"),
        JointKind.RABBET: _tr("Falz"),
        JointKind.TENON: _tr("Zapfen"),
        JointKind.FINGER: _tr("Fingerzinken"),
        JointKind.DOVETAIL: _tr("Schwalbenschwanz"),
    }
    return labels[kind]


def joint_param_label(name: str) -> str:
    labels = {
        "diameter": _tr("Durchmesser"),
        "length": _tr("Länge"),
        "depth_main": _tr("Bohrtiefe Hauptteil"),
        "edge_distance": _tr("Randabstand"),
        "max_spacing": _tr("Max. Abstand"),
        "count": _tr("Anzahl (0 = automatisch)"),
        "depth": _tr("Tiefe"),
        "play": _tr("Spiel"),
        "thickness": _tr("Zapfendicke (0 = ⅓)"),
        "shoulder": _tr("Absatz seitlich"),
        "slope": _tr("Neigung 1 :"),
        "pin_width": _tr("Zinkenbreite außen"),
    }
    return labels[name]


def joint_error_message(code: JointErrorCode) -> str:
    messages = {
        JointErrorCode.NO_CONTACT: _tr("Die Teile berühren sich nicht."),
        JointErrorCode.FACE_CONTACT: _tr(
            "Das Anschlussteil liegt mit der Fläche an, es braucht eine Kante "
            "(Hauptteil und Anschlussteil tauschen?)."
        ),
        JointErrorCode.MAIN_EDGE: _tr(
            "Das Anschlussteil muss auf der Fläche des Hauptteils stehen, nicht an seiner Kante."
        ),
        JointErrorCode.TOO_DEEP: _tr("Zu tief für die Materialstärke."),
        JointErrorCode.SCREW_TOO_SHORT: _tr("Die Schraube ist zu kurz."),
        JointErrorCode.INVALID_VALUE: _tr("Ein Wert ist ungültig."),
        JointErrorCode.ROTATED: _tr("Frei gedrehte Teile werden nicht automatisch verbunden."),
        JointErrorCode.ORIENTATION: _tr("Die Teile liegen nicht passend für diesen Beschlag."),
    }
    return messages[code]


def machining_text(holes: int, cutouts: int) -> str:
    parts = []
    if holes:
        parts.append(_tr("{n} Bohrungen").format(n=holes) if holes > 1 else _tr("1 Bohrung"))
    if cutouts:
        parts.append(_tr("{n} Fräsungen").format(n=cutouts) if cutouts > 1 else _tr("1 Fräsung"))
    return ", ".join(parts)


def fitting_kind_label(kind: FittingKind) -> str:
    labels = {
        FittingKind.HINGE: _tr("Topfband"),
        FittingKind.SLIDE: _tr("Schubkastenschiene"),
        FittingKind.SHELF_PINS: _tr("Lochreihe"),
        FittingKind.HANDLE: _tr("Griff"),
    }
    return labels[kind]


def fitting_param_label(name: str) -> str:
    labels = {
        "cup_diameter": _tr("Topfdurchmesser"),
        "cup_depth": _tr("Topftiefe"),
        "cup_distance": _tr("Topfmitte vom Rand"),
        "end_distance": _tr("Abstand oben/unten"),
        "count": _tr("Anzahl (0 = nach Höhe)"),
        "plate_distance": _tr("Montageplatte hinter Vorderkante"),
        "plate_hole_diameter": _tr("Bohrung Montageplatte"),
        "plate_hole_depth": _tr("Bohrtiefe Montageplatte"),
        "length": _tr("Nennlänge (0 = passend)"),
        "front_distance": _tr("Abstand vorne"),
        "back_distance": _tr("Abstand hinten"),
        "hole_diameter": _tr("Bohrung Korpus"),
        "hole_depth": _tr("Bohrtiefe Korpus"),
        "part_hole_diameter": _tr("Vorbohrung Zarge"),
        "part_hole_depth": _tr("Bohrtiefe Zarge"),
        "diameter": _tr("Durchmesser"),
        "depth": _tr("Tiefe"),
        "spacing": _tr("Lochabstand"),
        "first": _tr("Erstes Loch über Unterkante"),
        "last": _tr("Letztes Loch unter Oberkante"),
        "hole_spacing": _tr("Bohrabstand (0 = Knopf)"),
        "edge_distance": _tr("Abstand zur freien Kante"),
        "top_distance": _tr("Unter Oberkante (0 = Mitte)"),
        "screw_diameter": _tr("Schraubenloch"),
        "grip_diameter": _tr("Griffstärke"),
        "projection": _tr("Griffhöhe"),
    }
    return labels[name]


def hardware_label(item: Hardware) -> str:
    if item.kind == FittingKind.HINGE:
        return _tr("Topfband {d} mm mit Montageplatte").format(d=number(item.diameter))
    if item.kind == FittingKind.SLIDE:
        return _tr("Schubkastenschiene NL {l} mm").format(l=number(item.length))
    if item.kind == FittingKind.HANDLE:
        if item.length <= 0:
            return _tr("Möbelknopf")
        return _tr("Möbelgriff BA {l} mm").format(l=number(item.length))
    if isinstance(item.kind, FittingKind):
        return fitting_kind_label(item.kind)
    return _tr("{kind} {d} × {l} mm").format(
        kind=joint_kind_label(item.kind), d=number(item.diameter), l=number(item.length)
    )


def side_label(axis: Axis, sign: int) -> str:
    """Which side of the furniture a face looks to (Y runs backwards)."""
    names = {
        (Axis.X, -1): _tr("links"),
        (Axis.X, 1): _tr("rechts"),
        (Axis.Y, -1): _tr("vorne"),
        (Axis.Y, 1): _tr("hinten"),
        (Axis.Z, -1): _tr("unten"),
        (Axis.Z, 1): _tr("oben"),
    }
    return names[axis, sign]


def edge_label(orientation: Orientation, edge: BandEdge) -> str:
    """E.g. "vorne (L1)": where the edge is on the furniture, and its code."""
    side = side_label(*band_edge_side(orientation, edge))
    return f"{side[:1].upper()}{side[1:]} ({edge.value.upper()})"
