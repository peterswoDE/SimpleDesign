"""Cutting optimisation (Zuschnittoptimierung).

2D - sheets and planks: guillotine packing
-------------------------------------------
Every cut runs straight through the remaining piece, like on a panel saw.
Sheet coordinates: ``x`` along the sheet *length* (= grain), ``y`` along its
width. A part whose grain matters keeps its length along ``x``; other parts
may be turned by 90°.

Algorithm: parts are placed one by one into the free rectangle that fits
them best (smallest leftover on the short side). The rest of that rectangle
is split by one straight cut into two new free rectangles. Several sort orders
and split rules are tried; the result with the fewest sheets (and then the
emptiest last sheet, i.e. the largest reusable offcut) wins.

Kerf (saw blade width): each part occupies ``size + kerf``. The usable area
is enlarged by one kerf, so a part may end exactly at the trimmed edge.

1D - bars (construction timber)
-------------------------------
First-fit decreasing: longest part first, into the first bar where it fits.
Each part needs ``length + crosscut allowance + kerf``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from simpledesign.core.materials.material import Material
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.machining import local_shape
from simpledesign.core.model.settings import CutSettings

EPS = 1e-6


# ====================================================================== parts
@dataclass(frozen=True, slots=True)
class Piece:
    """One part to cut (pattern copies are separate pieces)."""

    name: str
    length: float  # along the grain
    width: float
    has_grain: bool = True


@dataclass(frozen=True, slots=True)
class Placement:
    name: str
    x: float  # along the sheet length
    y: float
    length: float  # extent along x
    width: float  # extent along y
    rotated: bool  # the part's length runs along y


@dataclass(slots=True)
class Sheet:
    length: float
    width: float
    trim: float
    placements: list[Placement] = field(default_factory=list)

    @property
    def area(self) -> float:
        return self.length * self.width

    @property
    def used_area(self) -> float:
        return sum(p.length * p.width for p in self.placements)


@dataclass(frozen=True, slots=True)
class BarCut:
    name: str
    x: float  # start along the bar
    length: float  # part length (without allowance)


@dataclass(slots=True)
class Bar:
    length: float
    cuts: list[BarCut] = field(default_factory=list)

    @property
    def used_length(self) -> float:
        return sum(c.length for c in self.cuts)


# ====================================================================== 2D
@dataclass(slots=True)
class _Free:
    x: float
    y: float
    length: float
    width: float


type _SortKey = Callable[[Piece], tuple[float, ...]]

_SORT_KEYS: tuple[_SortKey, ...] = (
    lambda p: (-p.length * p.width, -max(p.length, p.width)),
    lambda p: (-max(p.length, p.width), -min(p.length, p.width)),
    lambda p: (-p.length, -p.width),
    lambda p: (-p.width, -p.length),
)


def _orientations(piece: Piece) -> list[tuple[float, float, bool]]:
    """(extent x, extent y, rotated) options for a piece."""
    options = [(piece.length, piece.width, False)]
    if not piece.has_grain and abs(piece.length - piece.width) > EPS:
        options.append((piece.width, piece.length, True))
    return options


def fits_sheet(piece: Piece, length: float, width: float, trim: float) -> bool:
    usable_l, usable_w = length - 2 * trim, width - 2 * trim
    return any(pl <= usable_l + EPS and pw <= usable_w + EPS for pl, pw, _ in _orientations(piece))


def _pack_2d(
    pieces: Sequence[Piece],
    length: float,
    width: float,
    trim: float,
    kerf: float,
    split_short: bool,
) -> list[Sheet]:
    sheets: list[Sheet] = []
    free: list[list[_Free]] = []  # free rectangles per sheet

    def new_sheet() -> None:
        sheets.append(Sheet(length, width, trim))
        free.append([_Free(trim, trim, length - 2 * trim + kerf, width - 2 * trim + kerf)])

    for piece in pieces:
        # (score, sheet index, rect index, extent x, extent y, rotated)
        best: tuple[tuple[int, float, float], int, int, float, float, bool] | None = None
        for _attempt in range(2):
            for s, rects in enumerate(free):
                for r, rect in enumerate(rects):
                    for pl, pw, rotated in _orientations(piece):
                        rest_l, rest_w = rect.length - pl - kerf, rect.width - pw - kerf
                        if rest_l < -EPS or rest_w < -EPS:
                            continue
                        # Prefer earlier sheets, then the tightest fit.
                        score = (s, min(rest_l, rest_w), max(rest_l, rest_w))
                        if best is None or score < best[0]:
                            best = (score, s, r, pl, pw, rotated)
            if best is not None:
                break
            new_sheet()
        if best is None:
            continue  # larger than a sheet - the caller filters these out
        _, s, r, pl, pw, rotated = best
        rect = free[s].pop(r)
        sheets[s].placements.append(Placement(piece.name, rect.x, rect.y, pl, pw, rotated))
        need_l, need_w = pl + kerf, pw + kerf
        rest_l, rest_w = rect.length - need_l, rect.width - need_w
        # One straight cut splits the rest of the rectangle into two pieces.
        horizontal = (rest_l < rest_w) if split_short else (rest_l >= rest_w)
        if horizontal:  # cut along x: the right piece is only as wide as the part
            right = _Free(rect.x + need_l, rect.y, rest_l, need_w)
            top = _Free(rect.x, rect.y + need_w, rect.length, rest_w)
        else:  # cut along y: the top piece is only as long as the part
            right = _Free(rect.x + need_l, rect.y, rest_l, rect.width)
            top = _Free(rect.x, rect.y + need_w, need_l, rest_w)
        free[s] += [f for f in (right, top) if f.length > kerf + EPS and f.width > kerf + EPS]
    return sheets


def pack_sheets(
    pieces: Sequence[Piece], length: float, width: float, trim: float, kerf: float
) -> tuple[list[Sheet], list[Piece]]:
    """Pack ``pieces`` onto as few sheets as possible.

    Returns the sheets and the pieces that are larger than a sheet.
    """
    fitting = [p for p in pieces if fits_sheet(p, length, width, trim)]
    too_large = [p for p in pieces if not fits_sheet(p, length, width, trim)]
    best: list[Sheet] | None = None
    for sort_key in _SORT_KEYS:
        ordered = sorted(fitting, key=sort_key)
        for split_short in (True, False):
            sheets = _pack_2d(ordered, length, width, trim, kerf, split_short)
            if best is None or _score(sheets) < _score(best):
                best = sheets
    return best or [], too_large


def _score(sheets: list[Sheet]) -> tuple[int, float]:
    return (len(sheets), sheets[-1].used_area if sheets else 0.0)


# ====================================================================== 1D
def pack_bars(
    pieces: Sequence[Piece], bar_length: float, kerf: float, allowance: float
) -> tuple[list[Bar], list[Piece]]:
    """First-fit decreasing. Returns the bars and the pieces longer than a bar."""
    too_large = [p for p in pieces if p.length + allowance > bar_length + EPS]
    fitting = [p for p in pieces if p.length + allowance <= bar_length + EPS]
    bars: list[Bar] = []
    ends: list[float] = []  # next free position per bar
    for piece in sorted(fitting, key=lambda p: -p.length):
        need = piece.length + allowance
        i = next((k for k, end in enumerate(ends) if end + need <= bar_length + EPS), None)
        if i is None:
            bars.append(Bar(bar_length))
            ends.append(0.0)
            i = len(bars) - 1
        bars[i].cuts.append(BarCut(piece.name, ends[i], piece.length))
        ends[i] += need + kerf
    return bars, too_large


# ====================================================================== plan
@dataclass(slots=True)
class MaterialPlan:
    """Cutting plan and demand for one material."""

    material: Material
    pieces: list[Piece]
    sheets: list[Sheet] = field(default_factory=list)
    bars: list[Bar] = field(default_factory=list)
    too_large: list[Piece] = field(default_factory=list)

    @property
    def planned(self) -> bool:
        """False when the material has no stock size - only the demand is known."""
        m = self.material
        return m.stock_length is not None and (m.is_linear or m.stock_width is not None)

    @property
    def stock_count(self) -> int:
        return len(self.bars) if self.material.is_linear else len(self.sheets)

    @property
    def demand(self) -> float:
        """Net demand of the parts: m² for sheets, running metres for bars."""
        if self.material.is_linear:
            return sum(p.length for p in self.pieces) / 1000
        return sum(p.length * p.width for p in self.pieces) / 1e6

    @property
    def utilisation(self) -> float | None:
        """Share of the bought stock that ends up in parts (0..1)."""
        if self.material.is_linear:
            total = sum(b.length for b in self.bars)
            used = sum(b.used_length for b in self.bars)
        else:
            total = sum(s.area for s in self.sheets)
            used = sum(s.used_area for s in self.sheets)
        return used / total if total else None

    @property
    def cost(self) -> float | None:
        if self.material.price is None or not self.planned:
            return None
        return self.stock_count * self.material.price


def pieces_by_material(doc: Document, evaluation: Evaluation) -> dict[str, list[Piece]]:
    result: dict[str, list[Piece]] = {}
    for board in doc.boards.values():
        for shape in evaluation.machining.parts[board.id]:
            (length, width, _), _signature = local_shape(shape, board.orientation)
            piece = Piece(board.name, length, width, doc.has_grain(board.id))
            result.setdefault(board.material_id, []).append(piece)
    return result


def plan_material(material: Material, pieces: list[Piece], settings: CutSettings) -> MaterialPlan:
    plan = MaterialPlan(material, pieces)
    if not plan.planned or material.stock_length is None:
        return plan
    if material.is_linear:
        plan.bars, plan.too_large = pack_bars(
            pieces, material.stock_length, settings.kerf, settings.crosscut_allowance
        )
    elif material.stock_width is not None:
        plan.sheets, plan.too_large = pack_sheets(
            pieces, material.stock_length, material.stock_width, material.edge_trim, settings.kerf
        )
    return plan


def optimize(doc: Document, evaluation: Evaluation) -> list[MaterialPlan]:
    """Cutting plans for all materials of the document, sorted by name."""
    plans = [
        plan_material(doc.materials[material_id], pieces, doc.cut_settings)
        for material_id, pieces in pieces_by_material(doc, evaluation).items()
    ]
    return sorted(plans, key=lambda p: p.material.name)
