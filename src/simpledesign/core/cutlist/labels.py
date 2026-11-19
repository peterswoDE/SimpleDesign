"""Part labels (Etiketten): one sticker per physical part.

Each label carries the cut list position, the part's name, the furniture it
belongs to, finished and rough size, material, grain direction, the banded
edges and a QR code with the same data. Labels are printed onto common A4
label sheets (:data:`LABEL_SHEETS`); the first labels of a sheet that is
already partly used can be skipped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import segno

from simpledesign.core.cutlist.cutlist import CutRow, build_cutlist, cut_size
from simpledesign.core.materials.edgeband import BandEdge
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.machining import Hole, local_shape
from simpledesign.core.params.expression import format_number

A4 = (210.0, 297.0)  # mm


@dataclass(frozen=True, slots=True)
class LabelSheet:
    """An A4 sheet of equal labels in a grid (all sizes in mm)."""

    id: str
    name: str
    columns: int
    rows: int
    width: float
    height: float
    left: float  # page edge to the first label
    top: float
    column_pitch: float  # label start to the next one
    row_pitch: float

    @property
    def per_sheet(self) -> int:
        return self.columns * self.rows

    def position(self, index: int) -> tuple[float, float]:
        """Top left corner of label ``index`` (0 = top left, row by row)."""
        index %= self.per_sheet
        row, column = divmod(index, self.columns)
        return self.left + column * self.column_pitch, self.top + row * self.row_pitch


def _grid(
    sheet_id: str, name: str, columns: int, rows: int, width: float, height: float
) -> LabelSheet:
    """Labels butting against each other, centred on the page."""
    left = (A4[0] - columns * width) / 2
    top = (A4[1] - rows * height) / 2
    return LabelSheet(sheet_id, name, columns, rows, width, height, left, top, width, height)


LABEL_SHEETS: tuple[LabelSheet, ...] = (
    _grid("a4-3x8-70x36", "A4, 3 × 8 Etiketten, 70 × 36 mm (z. B. Zweckform 3475)", 3, 8, 70, 36),
    _grid("a4-3x8-70x35", "A4, 3 × 8 Etiketten, 70 × 35 mm (z. B. Zweckform 3422)", 3, 8, 70, 35),
    _grid(
        "a4-3x7-70x42", "A4, 3 × 7 Etiketten, 70 × 42,3 mm (z. B. Zweckform 3652)", 3, 7, 70, 42.3
    ),
    _grid(
        "a4-2x6-105x48", "A4, 2 × 6 Etiketten, 105 × 48 mm (z. B. Zweckform 3424)", 2, 6, 105, 48
    ),
    _grid(
        "a4-2x5-105x57", "A4, 2 × 5 Etiketten, 105 × 57 mm (z. B. Zweckform 3425)", 2, 5, 105, 57
    ),
    LabelSheet(
        "a4-2x7-99x38",
        "A4, 2 × 7 Etiketten, 99,1 × 38,1 mm (z. B. Avery L7163)",
        2,
        7,
        99.1,
        38.1,
        4.65,
        15.15,
        101.6,
        38.1,
    ),
)


def label_sheet(sheet_id: str) -> LabelSheet:
    for sheet in LABEL_SHEETS:
        if sheet.id == sheet_id:
            return sheet
    return LABEL_SHEETS[0]


type Size = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class PartLabel:
    position: int  # 1-based row of the cut list
    copy: int  # 1-based copy of this position
    copies: int
    name: str
    furniture: str  # document title, plus the group when there is one
    finished: Size  # length (along the grain), width, thickness
    rough: Size
    material: str
    has_grain: bool
    edges: tuple[tuple[BandEdge, str], ...]  # in the length/width frame
    holes: int

    @staticmethod
    def size_text(size: Size, number: Callable[[float], str] = format_number) -> str:
        return " × ".join(number(round(v, 1)) for v in size)

    def qr_text(self, number: Callable[[float], str] = format_number) -> str:
        """What the QR code holds: the label's data as plain text lines."""
        lines = [
            f"Pos. {self.position} ({self.copy}/{self.copies})",
            self.name,
            self.furniture,
            f"{self.size_text(self.finished, number)} mm",
            self.material,
        ]
        if self.edges:
            lines.append(", ".join(f"{edge.value.upper()}: {band}" for edge, band in self.edges))
        return "\n".join(line for line in lines if line)


def qr_matrix(text: str) -> list[list[bool]]:
    """Dark modules of a QR code (error correction M, quiet zone not included)."""
    code = segno.make(text, error="m", micro=False)
    return [[bool(module) for module in row] for row in code.matrix]


def _row_of(
    rows: list[CutRow],
    name: str,
    material_id: str,
    rough: Size,
    edges: tuple[tuple[BandEdge, str], ...],
    machining: tuple[int, int],
) -> int:
    """Index of the cut list row that holds this part."""
    for i, row in enumerate(rows):
        if (
            name in row.names
            and row.material_id == material_id
            and all(
                abs(a - b) < 0.06
                for a, b in zip(rough, (row.length, row.width, row.thickness), strict=True)
            )
            and row.edges == edges
            and (row.holes, row.cutouts) == machining
        ):
            return i
    raise LookupError(name)


def build_labels(
    doc: Document,
    evaluation: Evaluation,
    title: str,
    board_ids: set[str] | None = None,
) -> list[PartLabel]:
    """One label per physical part (all parts, or those of ``board_ids``),
    in cut list order."""
    rows = build_cutlist(doc, evaluation)
    found: list[tuple[int, str, str, Size, Size, bool, tuple[tuple[BandEdge, str], ...], int]] = []
    for board in doc.boards.values():
        if board_ids is not None and board.id not in board_ids:
            continue
        grain = doc.has_grain(board.id)
        group = doc.groups[board.group_id].name if board.group_id else ""
        furniture = " – ".join(part for part in (title, group) if part)
        for shape in evaluation.machining.parts[board.id]:
            rough, _signature, edges = cut_size(doc, board, shape)
            holes = sum(isinstance(f, Hole) for f in shape.features)
            position = _row_of(
                rows, board.name, board.material_id, rough, edges, (holes, shape.cut_operations)
            )
            # finished = rough plus the banding across each direction
            length_bands = sum(
                doc.edge_band(band_id).thickness
                for edge, band_id in board.edges.items()
                if not edge.along_length
            )
            width_bands = sum(
                doc.edge_band(band_id).thickness
                for edge, band_id in board.edges.items()
                if edge.along_length
            )
            (length, width, _t), _sig = local_shape(shape, board.orientation)
            if not grain and width - width_bands > length - length_bands:
                length_bands, width_bands = width_bands, length_bands  # turned like cut_size
            finished = (rough[0] + length_bands, rough[1] + width_bands, rough[2])
            found.append((position, board.name, furniture, finished, rough, grain, edges, holes))

    found.sort(key=lambda f: (f[0], f[1]))
    totals: dict[int, int] = {}
    for item in found:
        totals[item[0]] = totals.get(item[0], 0) + 1
    seen: dict[int, int] = {}
    labels = []
    for position, name, furniture, finished, rough, grain, edges, holes in found:
        seen[position] = seen.get(position, 0) + 1
        labels.append(
            PartLabel(
                position=position + 1,
                copy=seen[position],
                copies=totals[position],
                name=name,
                furniture=furniture,
                finished=finished,
                rough=rough,
                material=rows[position].material,
                has_grain=grain,
                edges=edges,
                holes=holes,
            )
        )
    return labels
