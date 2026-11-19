"""Cut list (Zuschnittliste).

Parts are merged into one row when material, dimensions and grain direction
are equal. When the grain matters, length and width stay apart (length =
along the grain). Without grain (MDF …, or a part with "ignore grain") a
300 x 600 part equals a 600 x 300 part, so we normalise to length >= width.

Sizes are *rough* sizes: a part that goes into a groove is longer than it
looks, a part with edge banding is cut smaller by the banding thickness.
Parts are only merged when their machining (holes, grooves …) and their
banding are the same as well.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass

from simpledesign.core.materials.edgeband import EDGE_EXCESS, BandEdge
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.machining import Hole, PartShape, local_shape
from simpledesign.core.model.part import Board
from simpledesign.core.params.expression import format_number

type CsvHeaders = tuple[str, str, str, str, str, str, str, str]


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
    # Banded edges (in the row's length/width frame) with the band name.
    edges: tuple[tuple[BandEdge, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BandDemand:
    """Edge banding needed of one kind."""

    band_id: str
    name: str
    thickness: float
    length: float  # mm, overhang included
    edges: int  # number of banded edges


def cut_size(
    doc: Document, board: Board, shape: PartShape
) -> tuple[tuple[float, float, float], tuple[object, ...], tuple[tuple[BandEdge, str], ...]]:
    """Rough (length, width, thickness) of one part, its machining signature and
    its banded edges - turned so length >= width when the grain does not matter."""
    (length, width, thickness), signature = local_shape(shape, board.orientation)
    edges = []
    for edge, band_id in board.edges.items():
        band = doc.edge_band(band_id)
        if edge.along_length:
            width -= band.thickness
        else:
            length -= band.thickness
        edges.append((edge, band.name))
    if not doc.has_grain(board.id) and width > length:
        length, width = width, length
        edges = [(edge.swapped(), name) for edge, name in edges]
    return (length, width, thickness), signature, tuple(sorted(edges))


def edges_summary(edges: tuple[tuple[BandEdge, str], ...]) -> str:
    """E.g. "L1, L2: ABS 2 mm weiß; W1: Furnier Eiche 0,5 mm" (grouped by band)."""
    by_band: dict[str, list[str]] = {}
    for edge, name in edges:
        by_band.setdefault(name, []).append(edge.value.upper())
    return "; ".join(f"{', '.join(codes)}: {name}" for name, codes in by_band.items())


def band_demand(doc: Document, evaluation: Evaluation) -> list[BandDemand]:
    """Running length of every edge banding (finished edge length + overhang)."""
    totals: dict[str, tuple[float, int]] = {}
    for board in doc.boards.values():
        if not board.edges:
            continue
        state = evaluation.boards.get(board.id)
        if state is None:
            continue
        copies = len(evaluation.machining.parts[board.id])
        for edge, band_id in board.edges.items():
            edge_length = state.length if edge.along_length else state.width
            length, count = totals.get(band_id, (0.0, 0))
            totals[band_id] = (length + copies * (edge_length + EDGE_EXCESS), count + copies)
    demand = []
    for band_id, (length, count) in totals.items():
        band = doc.edge_band(band_id)
        demand.append(BandDemand(band_id, band.name, band.thickness, length, count))
    return sorted(demand, key=lambda d: d.name)


def build_cutlist(doc: Document, evaluation: Evaluation) -> list[CutRow]:
    type Key = tuple[
        str, float, float, float, bool, tuple[object, ...], tuple[tuple[BandEdge, str], ...]
    ]
    parts_by_key: dict[Key, list[str]] = {}
    counts: dict[Key, tuple[int, int]] = {}
    for board in doc.boards.values():
        grain = doc.has_grain(board.id)
        for shape in evaluation.machining.parts[board.id]:
            (length, width, thickness), signature, edges = cut_size(doc, board, shape)
            key = (
                board.material_id,
                round(length, 1),
                round(width, 1),
                round(thickness, 1),
                grain,
                signature,
                edges,
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
            edges=edges,
        )
        for key, names in parts_by_key.items()
        for material_id, length, width, thickness, grain, _, edges in [key]
    ]
    rows.sort(key=lambda r: (r.material, -r.thickness, -r.length, -r.width, r.holes, r.cutouts))
    return rows


def _decimal(value: float) -> str:
    """German spreadsheet style: decimal comma."""
    return format_number(value).replace(".", ",")


def to_csv(
    rows: list[CutRow],
    headers: CsvHeaders,
    machining: Callable[[CutRow], str],
    edges: Callable[[CutRow], str],
) -> str:
    """CSV for Excel/LibreOffice (``;`` separated, decimal comma).

    ``headers`` and the ``machining``/``edges`` descriptions come from the UI so
    they are translated: (quantity, length, width, thickness, material,
    machining, edges, parts).
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
                edges(row),
                ", ".join(row.names),
            ]
        )
    return buffer.getvalue()
