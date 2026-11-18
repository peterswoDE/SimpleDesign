"""Translatable texts and number display shared by several widgets."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale

from simpledesign.core.model.part import Axis, Orientation
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import format_number


def _tr(text: str) -> str:
    return QCoreApplication.translate("Texts", text)


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
    }
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
    }
    return labels[prop]
