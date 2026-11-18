"""Cut list (Zuschnittliste).

Parts are merged into one row when material, dimensions and grain direction
are equal. When the grain matters, length and width stay apart (length =
along the grain). Without grain (MDF …, or a part with "ignore grain") a
300 x 600 part equals a 600 x 300 part, so we normalise to length >= width.
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
    material_id: str
    material: str
    length: float
    width: float
    thickness: float
    quantity: int
    names: tuple[str, ...]
    has_grain: bool


def build_cutlist(doc: Document, evaluation: Evaluation) -> list[CutRow]:
    type Key = tuple[str, float, float, float, bool]
    parts_by_key: dict[Key, list[tuple[str, int]]] = {}
    for board in doc.boards.values():
        state = evaluation.boards[board.id]
        grain = doc.has_grain(board.id)
        length, width = state.length, state.width
        if not grain and width > length:
            length, width = width, length
        key = (
            board.material_id,
            round(length, 1),
            round(width, 1),
            round(state.thickness, 1),
            grain,
        )
        parts_by_key.setdefault(key, []).append((board.name, state.count))

    rows = [
        CutRow(
            material_id=material_id,
            material=doc.materials[material_id].name,
            length=length,
            width=width,
            thickness=thickness,
            quantity=sum(count for _, count in parts),
            names=tuple(sorted(name for name, _ in parts)),
            has_grain=grain,
        )
        for (material_id, length, width, thickness, grain), parts in parts_by_key.items()
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
