"""Cut list (Zuschnittliste).

Parts are merged into one row when material, dimensions and grain direction
are equal. When the grain matters, length and width stay apart (length =
along the grain). Without grain (MDF …, or a part with "ignore grain") a
300 x 600 part equals a 600 x 300 part, so we normalise to length >= width.

Sizes are *rough* sizes: a part that goes into a groove is longer than it
looks. Parts are only merged when their machining (holes, grooves …) is the
same as well.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.machining import Hole, local_shape
from simpledesign.core.params.expression import format_number

type CsvHeaders = tuple[str, str, str, str, str, str, str]


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
    holes: int = 0  # per part
    cutouts: int = 0  # grooves, mortises, tenons … per part


def build_cutlist(doc: Document, evaluation: Evaluation) -> list[CutRow]:
    type Key = tuple[str, float, float, float, bool, tuple[object, ...]]
    parts_by_key: dict[Key, list[str]] = {}
    counts: dict[Key, tuple[int, int]] = {}
    for board in doc.boards.values():
        grain = doc.has_grain(board.id)
        for shape in evaluation.machining.parts[board.id]:
            (length, width, thickness), signature = local_shape(shape, board.orientation)
            if not grain and width > length:
                length, width = width, length
            key = (
                board.material_id,
                round(length, 1),
                round(width, 1),
                round(thickness, 1),
                grain,
                signature,
            )
            parts_by_key.setdefault(key, []).append(board.name)
            holes = sum(isinstance(f, Hole) for f in shape.features)
            counts[key] = (holes, shape.cut_operations)

    rows = [
        CutRow(
            material_id=material_id,
            material=doc.materials[material_id].name,
            length=length,
            width=width,
            thickness=thickness,
            quantity=len(names),
            names=tuple(sorted(set(names))),
            has_grain=grain,
            holes=counts[key][0],
            cutouts=counts[key][1],
        )
        for key, names in parts_by_key.items()
        for material_id, length, width, thickness, grain, _ in [key]
    ]
    rows.sort(key=lambda r: (r.material, -r.thickness, -r.length, -r.width, r.holes, r.cutouts))
    return rows


def _decimal(value: float) -> str:
    """German spreadsheet style: decimal comma."""
    return format_number(value).replace(".", ",")


def to_csv(rows: list[CutRow], headers: CsvHeaders, machining: Callable[[CutRow], str]) -> str:
    """CSV for Excel/LibreOffice (``;`` separated, decimal comma).

    ``headers`` and the ``machining`` description come from the UI so they are
    translated: (quantity, length, width, thickness, material, machining, parts).
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
                machining(row),
                ", ".join(row.names),
            ]
        )
    return buffer.getvalue()
