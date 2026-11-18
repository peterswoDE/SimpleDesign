"""Drawing sheets: paper-space primitives, dimensions and the title block.

Everything is in paper millimetres, origin bottom left, y up. Renderers
(PDF/preview via Qt, DXF via ezdxf) only need :class:`Polyline` and
:class:`Text`; dimensions are expanded into those by :func:`expand`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

type P2 = tuple[float, float]


class Style(StrEnum):
    VISIBLE = "visible"  # thick, solid
    HIDDEN = "hidden"  # thin, dashed
    THIN = "thin"  # dimension and helper lines
    FRAME = "frame"  # border and title block


class Align(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class PaperSize(StrEnum):
    A4 = "A4"
    A3 = "A3"

    @property
    def landscape(self) -> P2:
        return {PaperSize.A4: (297.0, 210.0), PaperSize.A3: (420.0, 297.0)}[self]


@dataclass(frozen=True, slots=True)
class Polyline:
    points: tuple[P2, ...]
    style: Style = Style.VISIBLE
    closed: bool = False


@dataclass(frozen=True, slots=True)
class Text:
    pos: P2  # baseline anchor
    text: str
    height: float = 3.5
    align: Align = Align.LEFT
    rotation: float = 0.0  # degrees, counter-clockwise
    bold: bool = False


@dataclass(frozen=True, slots=True)
class Dimension:
    """Linear dimension between ``a`` and ``b`` measured along ``axis``.

    ``axis`` 0 = horizontal, 1 = vertical. ``at`` is the paper coordinate of
    the dimension line (y for horizontal, x for vertical). ``text`` is the
    shown value (model mm, already scaled back).
    """

    a: P2
    b: P2
    axis: int
    at: float
    text: str


Item = Polyline | Text | Dimension

TEXT_HEIGHT = 3.5
DIM_TEXT = 2.5
ARROW = 2.5  # arrow length
EXTENSION_GAP = 1.0  # gap between the part and an extension line
EXTENSION_OVERSHOOT = 1.5


@dataclass(slots=True)
class Sheet:
    paper: PaperSize
    title: str
    items: list[Item] = field(default_factory=list)
    # Model-to-paper transforms of the views, for adding dimensions later.
    views: list[ViewPlacement] = field(default_factory=list)
    scale: float = 1.0  # main scale, shown in the title block
    fields: list[tuple[str, str]] = field(default_factory=list)  # extra title fields

    @property
    def size(self) -> P2:
        return self.paper.landscape


@dataclass(frozen=True, slots=True)
class ViewPlacement:
    """Where a view sits on the sheet: paper = offset + model * scale."""

    offset: P2
    scale: float  # paper mm per model mm
    lo: P2  # paper bounds of the view
    hi: P2

    def to_model_length(self, paper: float) -> float:
        return paper / self.scale


def expand(item: Item) -> list[Polyline | Text]:
    """Dimension -> extension lines, dimension line, arrows and the value."""
    if not isinstance(item, Dimension):
        return [item]
    a, b, axis, at = item.a, item.b, item.axis, item.at
    k = axis
    o = 1 - axis
    out: list[Polyline | Text] = []
    for p in (a, b):
        sign = 1.0 if at >= p[o] else -1.0
        start = p[o] + sign * EXTENSION_GAP
        end = at + sign * EXTENSION_OVERSHOOT
        if (end - start) * sign > 0:
            out.append(Polyline((_pt(k, p[k], start), _pt(k, p[k], end)), Style.THIN))
    lo, hi = sorted((a[k], b[k]))
    out.append(Polyline((_pt(k, lo, at), _pt(k, hi, at)), Style.THIN))
    length = hi - lo
    inside = length > 2.5 * ARROW
    for x, direction in ((lo, -1.0), (hi, 1.0)):
        d = direction if not inside else -direction  # arrows point at the lines
        tip = _pt(k, x, at)
        for side in (-1.0, 1.0):
            tail = _pt(k, x + d * ARROW, at + side * ARROW / 3)
            out.append(Polyline((tail, tip), Style.THIN))
        if not inside:
            out.append(Polyline((tip, _pt(k, x + direction * ARROW * 1.6, at)), Style.THIN))
    text_width = len(item.text) * DIM_TEXT * 0.65
    if length > text_width + 2 * ARROW:
        mid, align = (lo + hi) / 2, Align.CENTER
    else:  # too short: write the value beyond the far arrow
        mid, align = hi + ARROW * 1.6 + 0.8, Align.LEFT
    if axis == 0:
        out.append(Text((mid, at + 0.8), item.text, DIM_TEXT, align))
    else:
        out.append(Text((at - 0.8, mid), item.text, DIM_TEXT, align, 90.0))
    return out


def _pt(axis: int, along: float, across: float) -> P2:
    return (along, across) if axis == 0 else (across, along)


def frame_and_title(
    sheet: Sheet, fields: list[tuple[str, str]], margin: float = 10.0
) -> tuple[P2, P2]:
    """Border, title block (bottom right). Returns the free drawing area."""
    w, h = sheet.size
    lo, hi = (margin + 10.0, margin), (w - margin, h - margin)  # wider left filing margin
    sheet.items.append(Polyline(_rect(lo, hi), Style.FRAME, closed=True))
    block_w = 120.0
    row = 7.0
    block_h = row * (len(fields) + 1)
    bx, by = hi[0] - block_w, lo[1]
    sheet.items.append(Polyline(_rect((bx, by), (hi[0], by + block_h)), Style.FRAME, closed=True))
    label_w = 30.0
    top = by + block_h
    sheet.items.append(Polyline(((bx, top - row), (hi[0], top - row)), Style.THIN))
    sheet.items.append(Text((bx + 2, top - row + 2), sheet.title, 4.0, bold=True))
    for i, (label, value) in enumerate(fields):
        y = top - row * (i + 2)
        if i < len(fields) - 1:
            sheet.items.append(Polyline(((bx, y), (hi[0], y)), Style.THIN))
        sheet.items.append(Polyline(((bx + label_w, y), (bx + label_w, y + row)), Style.THIN))
        sheet.items.append(Text((bx + 2, y + 2.2), label, 2.5))
        sheet.items.append(Text((bx + label_w + 2, y + 2.2), value, 3.0))
    # The free area excludes the title block height along the full width only
    # where it is needed: views are laid out above it.
    return (lo[0] + 5, lo[1] + block_h + 5), (hi[0] - 5, hi[1] - 5)


def _rect(lo: P2, hi: P2) -> tuple[P2, ...]:
    return (lo, (hi[0], lo[1]), hi, (lo[0], hi[1]))


STANDARD_SCALES = (
    5.0,
    2.0,
    1.0,
    1 / 2,
    1 / 2.5,
    1 / 5,
    1 / 10,
    1 / 20,
    1 / 25,
    1 / 50,
    1 / 100,
    1 / 200,
)


def scale_text(scale: float) -> str:
    if scale >= 1:
        return f"{scale:g}:1"
    return f"1:{1 / scale:g}"


def pick_scale(needed: float) -> float:
    """Largest standard scale not above ``needed`` (paper per model)."""
    for scale in STANDARD_SCALES:
        if scale <= needed + 1e-9:
            return scale
    return needed


def dimension_text(model_length: float) -> str:
    rounded = round(model_length, 1)
    text = f"{rounded:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def circle_points(center: P2, radius: float, segments: int = 48) -> tuple[P2, ...]:
    return tuple(
        (
            center[0] + radius * math.cos(2 * math.pi * i / segments),
            center[1] + radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments)
    )
