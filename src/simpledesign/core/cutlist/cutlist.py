"""Cut list (Zuschnittliste).

Parts are merged into one row when material, dimensions and grain direction
are equal (plan, phase 1). For materials *with* grain, length and width stay
apart (length = along the grain). Without grain (MDF …) a 300 x 600 part equals
a 600 x 300 part, so we normalise to length >= width.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.params.expression import format_number

type CsvHeaders = tuple[str, str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class CutRow:
    material: str
    length: float
    width: float
    thickness: float
    quantity: int
    names: tuple[str, ...]
    has_grain: bool


def build_cutlist(doc: Document, evaluation: Evaluation) -> list[CutRow]:
    parts_by_key: dict[tuple[str, float, float, float], list[tuple[str, int]]] = {}
    grain_of: dict[str, bool] = {}
    for board in doc.boards.values():
        state = evaluation.boards[board.id]
        material = doc.materials[board.material_id]
        grain_of[material.name] = material.has_grain
        length, width = state.length, state.width
        if not material.has_grain and width > length:
            length, width = width, length
        key = (material.name, round(length, 1), round(width, 1), round(state.thickness, 1))
        parts_by_key.setdefault(key, []).append((board.name, state.count))

    rows = [
        CutRow(
            material=material,
            length=length,
            width=width,
            thickness=thickness,
            quantity=sum(count for _, count in parts),
            names=tuple(sorted(name for name, _ in parts)),
            has_grain=grain_of[material],
        )
        for (material, length, width, thickness), parts in parts_by_key.items()
    ]
    rows.sort(key=lambda r: (r.material, -r.thickness, -r.length, -r.width))
    return rows


def _decimal(value: float) -> str:
    """German spreadsheet style: decimal comma."""
    return format_number(value).replace(".", ",")


def to_csv(rows: list[CutRow], headers: CsvHeaders) -> str:
    """CSV for Excel/LibreOffice (``;`` separated, decimal comma).

    ``headers`` come from the UI so they are translated:
    (quantity, length, width, thickness, material, parts).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(
            [
                row.quantity,
                _decimal(row.length),
                _decimal(row.width),
                _decimal(row.thickness),
                row.material,
                ", ".join(row.names),
            ]
        )
    return buffer.getvalue()
