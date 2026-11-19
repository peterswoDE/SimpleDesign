"""Lay out drawing sheets: an overview sheet and one sheet per cut-list part.

Views follow projection method 1 (European): the front view top left, the
view from the left to its right, the view from above below it. Each view
carries its overall dimensions. Part sheets show the face (along the
thickness), two edge views and a hole table.
"""

from __future__ import annotations

from dataclasses import dataclass

from OCP.TopoDS import TopoDS_Shape

from simpledesign.core.cutlist.cutlist import CutRow, build_cutlist, cut_size, edges_summary
from simpledesign.core.drawing.projection import (
    STANDARD_VIEWS,
    Projection,
    View,
    ViewKind,
    project,
)
from simpledesign.core.drawing.sheet import (
    Align,
    Dimension,
    PaperSize,
    Polyline,
    Sheet,
    Style,
    Text,
    ViewPlacement,
    dimension_text,
    frame_and_title,
    pick_scale,
    scale_text,
)
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.machining import Hole, PartShape, local_shape
from simpledesign.core.model.part import Orientation
from simpledesign.core.model.state import Vec3

type P2 = tuple[float, float]

GAP = 18.0  # paper mm between views (room for one dimension row)
DIM_OFFSET = 8.0  # dimension line distance from the view
TABLE_COLUMNS = (7.0, 14.0, 14.0, 12.0, 12.0, 12.0)
TABLE_WIDTH = sum(TABLE_COLUMNS)


@dataclass(frozen=True, slots=True)
class DrawingLabels:
    """Translated texts of the title block; the UI fills them in."""

    overview: str  # sheet title of the overview, e.g. "Übersicht"
    scale: str
    date: str
    sheet: str  # "Blatt"
    sheet_of: str  # "{n} von {m}"
    material: str
    quantity: str
    size: str  # "Maß (L × B × D)"
    parts: str  # used on the overview: number of parts
    holes: str  # hole table heading
    hole_columns: tuple[str, str, str, str, str, str]  # Nr, X, Y, Z, Ø, Tiefe
    edges: str = "Kanten"  # edge banding field


@dataclass(frozen=True, slots=True)
class DrawingOptions:
    paper: PaperSize = PaperSize.A4
    overview: bool = True
    parts: bool = True
    hidden_lines: bool = True


def build_sheets(
    doc: Document,
    evaluation: Evaluation,
    labels: DrawingLabels,
    options: DrawingOptions,
    title: str,
    date: str,
) -> list[Sheet]:
    sheets: list[Sheet] = []
    if options.overview:
        sheets.append(overview_sheet(doc, evaluation, labels, options, title))
    if options.parts:
        for row in build_cutlist(doc, evaluation):
            found = _row_shape(doc, evaluation, row)
            if found is not None:
                sheets.append(part_sheet(row, *found, labels, options))
    for i, sheet in enumerate(sheets):
        _finish_title_block(sheet, labels, date, i + 1, len(sheets))
    return sheets


# ====================================================================== overview
def overview_sheet(
    doc: Document,
    evaluation: Evaluation,
    labels: DrawingLabels,
    options: DrawingOptions,
    title: str,
) -> Sheet:
    from simpledesign.core.geometry.mesh import make_shape
    from simpledesign.core.geometry.placement import placed

    shapes: list[TopoDS_Shape] = []
    count = 0
    for board in doc.boards.values():
        if not doc.is_visible(board):
            continue
        for part in evaluation.machining.parts[board.id]:
            shapes.append(placed(make_shape(part.size, part.features), part.origin, part.rotation))
            count += 1
    sheet = Sheet(options.paper, title)
    sheet.fields = [(labels.parts, str(count))]
    views = {kind: project(shapes, STANDARD_VIEWS[kind], options.hidden_lines) for kind in ViewKind}
    _place_three_views(
        sheet,
        labels,
        views[ViewKind.FRONT],
        views[ViewKind.LEFT],
        views[ViewKind.TOP],
        views[ViewKind.ISO],
        n_fields=4,
        reserve_right=0.0,
    )
    return sheet


# ====================================================================== parts
def _row_shape(
    doc: Document, evaluation: Evaluation, row: CutRow
) -> tuple[PartShape, Orientation] | None:
    for board in doc.boards.values():
        if board.name not in row.names or board.material_id != row.material_id:
            continue
        for shape in evaluation.machining.parts[board.id]:
            (length, width, thickness), _sig, edges = cut_size(doc, board, shape)
            if (
                abs(length - row.length) < 0.06
                and abs(width - row.width) < 0.06
                and abs(thickness - row.thickness) < 0.06
                and edges == row.edges
            ):
                return shape, board.orientation
    return None


def _unit(axis: int, sign: float = 1.0) -> Vec3:
    v = [0.0, 0.0, 0.0]
    v[axis] = sign
    return (v[0], v[1], v[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _neg(a: Vec3) -> Vec3:
    return (-a[0], -a[1], -a[2])


def part_views(
    shape: PartShape, orientation: Orientation, swap: bool = False
) -> tuple[View, View, View]:
    """Face view (length right, width up), edge from above, edge from the left."""
    l_axis = orientation.length_axis.position
    w_axis = orientation.width_axis.position
    if swap:
        l_axis, w_axis = w_axis, l_axis
    right, up = _unit(l_axis), _unit(w_axis)
    normal = _cross(right, up)
    face = View(normal, right)
    from_above = View(up, right)  # placed below the face view
    from_left = View(_neg(right), normal)  # placed right of the face view
    return face, from_above, from_left


def part_sheet(
    row: CutRow,
    shape: PartShape,
    orientation: Orientation,
    labels: DrawingLabels,
    options: DrawingOptions,
) -> Sheet:
    from simpledesign.core.geometry.mesh import make_shape

    solid = make_shape(shape.size, shape.features)
    (length, width, _t), _sig = local_shape(shape, orientation)
    swap = not row.has_grain and width > length
    face, above, left = part_views(shape, orientation, swap)
    projections = [project([solid], v, options.hidden_lines) for v in (face, above, left)]
    title = ", ".join(row.names)
    sheet = Sheet(options.paper, title)
    size = " × ".join(dimension_text(v) for v in (row.length, row.width, row.thickness))
    sheet.fields = [
        (labels.material, row.material),
        (labels.quantity, str(row.quantity)),
        (labels.size, size),
    ]
    if row.edges:
        sheet.fields.append((labels.edges, edges_summary(row.edges)))
    holes = [f for f in shape.features if isinstance(f, Hole)]
    face_place = _place_three_views(
        sheet,
        labels,
        projections[0],
        projections[2],
        projections[1],
        None,
        n_fields=6,
        reserve_right=TABLE_WIDTH + 5 if holes else 0.0,
    )
    if holes:
        _hole_table(sheet, labels, holes, face, face_place, shape.size, swap, orientation)
    _edge_marks(sheet, row, shape, face, face_place)
    return sheet


EDGE_MARK_GAP = 1.5  # paper mm between the part outline and a banding mark


def _edge_marks(
    sheet: Sheet, row: CutRow, shape: PartShape, face: View, place: ViewPlacement
) -> None:
    """A thick line beside every banded edge of the face view."""
    if not row.edges:
        return
    corners = [
        face.to_2d(
            (
                shape.origin[0] + i * shape.size[0],
                shape.origin[1] + j * shape.size[1],
                shape.origin[2] + k * shape.size[2],
            )
        )
        for i in (0, 1)
        for j in (0, 1)
        for k in (0, 1)
    ]
    us = [place.offset[0] + u * place.scale for u, _v in corners]
    vs = [place.offset[1] + v * place.scale for _u, v in corners]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    g = EDGE_MARK_GAP
    lines = {
        "l1": ((u0, v0 - g), (u1, v0 - g)),
        "l2": ((u0, v1 + g), (u1, v1 + g)),
        "w1": ((u0 - g, v0), (u0 - g, v1)),
        "w2": ((u1 + g, v0), (u1 + g, v1)),
    }
    for edge, _name in row.edges:
        sheet.items.append(Polyline(lines[edge.value], Style.VISIBLE))


def _hole_table(
    sheet: Sheet,
    labels: DrawingLabels,
    holes: list[Hole],
    face: View,
    place: ViewPlacement,
    size: Vec3,
    swap: bool,
    orientation: Orientation,
) -> None:
    perm = [
        orientation.length_axis.position,
        orientation.width_axis.position,
        orientation.thickness_axis.position,
    ]
    if swap:
        perm[0], perm[1] = perm[1], perm[0]
    rows: list[tuple[str, ...]] = []
    for i, hole in enumerate(sorted(holes, key=lambda h: [h.center[p] for p in perm])):
        local = [hole.center[p] for p in perm]
        rows.append(
            (
                str(i + 1),
                *(dimension_text(v) for v in local),
                "Ø" + dimension_text(hole.diameter),
                dimension_text(hole.depth),
            )
        )
        if hole.axis == perm[2]:  # visible on the face: number it
            u, v = face.to_2d(hole.center)
            x = place.offset[0] + u * place.scale
            y = place.offset[1] + v * place.scale
            r = hole.diameter / 2 * place.scale
            sheet.items.append(Text((x + r + 0.6, y + r + 0.3), str(i + 1), 2.0))
    # Table at the top right of the free area, as many rows as fit.
    w, h = sheet.size
    col = TABLE_COLUMNS
    total = sum(col)
    x0 = w - 15 - total
    y = h - 15
    sheet.items.append(Text((x0, y), labels.holes, 3.0, bold=True))
    y -= 5
    max_rows = 30
    for line in [labels.hole_columns, *rows[:max_rows]]:
        x = x0
        for width, cell in zip(col, line, strict=True):
            sheet.items.append(Text((x + width - 1, y), cell, 2.2, Align.RIGHT))
            x += width
        sheet.items.append(Polyline(((x0, y - 1.2), (x0 + total, y - 1.2)), Style.THIN))
        y -= 4.2
    if len(rows) > max_rows:
        sheet.items.append(Text((x0, y), f"… (+{len(rows) - max_rows})", 2.2))


# ====================================================================== shared layout
def _place_three_views(
    sheet: Sheet,
    labels: DrawingLabels,
    front: Projection,
    side: Projection,
    below: Projection,
    iso: Projection | None,
    n_fields: int,
    reserve_right: float,
) -> ViewPlacement:
    """Front top left, side right of it, ``below`` under it, iso bottom right."""
    w, h = sheet.size
    margin = 10.0
    lo = (margin + 10 + GAP, margin + 7.0 * (n_fields + 1) + 4)
    hi = (w - margin - 5 - reserve_right, h - margin - GAP)
    fl, sl, bl = (_extent(p)[0] for p in (front, side, below))
    avail_w = hi[0] - lo[0] - GAP
    avail_h = hi[1] - lo[1] - GAP
    need_w = fl[0] + sl[0]
    need_h = fl[1] + bl[1]
    needed = min(
        avail_w / need_w if need_w > 0 else 10.0,
        avail_h / need_h if need_h > 0 else 10.0,
    )
    scale = pick_scale(needed)
    sheet.scale = scale
    left = lo[0]
    top = hi[1]
    front_place = _put(sheet, front, scale, (left, top - fl[1] * scale))
    side_x = left + fl[0] * scale + GAP
    _put(sheet, side, scale, (side_x, top - sl[1] * scale), vertical=False)
    below_y = top - fl[1] * scale - GAP - bl[1] * scale
    _put(sheet, below, scale, (left, below_y), horizontal=False)
    if iso is not None:
        il = _extent(iso)[0]
        cell_lo = (side_x, lo[1])
        cell_hi = (hi[0], below_y + bl[1] * scale)
        cw, ch = cell_hi[0] - cell_lo[0], cell_hi[1] - cell_lo[1]
        if il[0] > 0 and il[1] > 0 and cw > 20 and ch > 20:
            iso_scale = min(scale, pick_scale(min(cw / il[0], ch / il[1])))
            x = cell_lo[0] + (cw - il[0] * iso_scale) / 2
            y = cell_lo[1] + (ch - il[1] * iso_scale) / 2
            _put(sheet, iso, iso_scale, (x, y), horizontal=False, vertical=False)
    return front_place


def _extent(p: Projection) -> tuple[P2, P2]:
    (u0, v0), (u1, v1) = p.bounds()
    return (u1 - u0, v1 - v0), (u0, v0)


def _put(
    sheet: Sheet,
    projection: Projection,
    scale: float,
    corner: P2,
    horizontal: bool = True,
    vertical: bool = True,
) -> ViewPlacement:
    """Draw ``projection`` with its lower-left bound at ``corner``; add the
    overall width (above) and height (left) dimensions."""
    (u0, v0), (u1, v1) = projection.bounds()
    offset = (corner[0] - u0 * scale, corner[1] - v0 * scale)

    def paper(line: list[P2]) -> tuple[P2, ...]:
        return tuple((offset[0] + u * scale, offset[1] + v * scale) for u, v in line)

    for line in projection.hidden:
        sheet.items.append(Polyline(paper(line), Style.HIDDEN))
    for line in projection.visible:
        sheet.items.append(Polyline(paper(line), Style.VISIBLE))
    lo = corner
    hi = (corner[0] + (u1 - u0) * scale, corner[1] + (v1 - v0) * scale)
    if horizontal and u1 - u0 > 1e-6:
        sheet.items.append(
            Dimension((lo[0], hi[1]), hi, 0, hi[1] + DIM_OFFSET, dimension_text(u1 - u0))
        )
    if vertical and v1 - v0 > 1e-6:
        sheet.items.append(
            Dimension(lo, (lo[0], hi[1]), 1, lo[0] - DIM_OFFSET, dimension_text(v1 - v0))
        )
    placement = ViewPlacement(offset, scale, lo, hi)
    sheet.views.append(placement)
    return placement


def _finish_title_block(sheet: Sheet, labels: DrawingLabels, date: str, n: int, m: int) -> None:
    fields = [
        *sheet.fields,
        (labels.scale, scale_text(sheet.scale)),
        (labels.date, date),
        (labels.sheet, labels.sheet_of.format(n=n, m=m)),
    ]
    frame_and_title(sheet, fields)
