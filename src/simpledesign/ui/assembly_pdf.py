"""Assembly instructions as PDF (Montageanleitung).

A4 portrait: the title and the fittings needed on top of the first page, then
one box per step with the part, the joints to the parts placed before and an
isometric picture (new part thick, hidden edges of it dashed).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen, QPolygonF

from simpledesign.core.drawing.assembly import AssemblyStep, StepPicture, assembly_steps
from simpledesign.core.drawing.projection import P2
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.fitting import FittingKind
from simpledesign.ui.texts import fitting_kind_label, hardware_label, joint_kind_label

PAGE = (210.0, 297.0)
MARGIN = 15.0
STEP_HEIGHT = 118.0  # mm per step box
TEXT_WIDTH = 62.0  # text column left of the picture


def _tr(text: str) -> str:
    return QCoreApplication.translate("AssemblyPdf", text)


def step_title(doc: Document, number: int, step: AssemblyStep) -> str:
    name = doc.boards[step.board_id].name
    if step.copies > 1:
        return _tr("Schritt {n}: {name} ({count}×)").format(n=number, name=name, count=step.copies)
    return _tr("Schritt {n}: {name}").format(n=number, name=name)


def step_lines(doc: Document, step: AssemblyStep) -> list[str]:
    """The joints of the step as text lines."""
    if not step.connections:
        return [_tr("Als Erstes hinlegen.")] if step.board_id else []
    lines = []
    for connection in step.connections:
        kind = connection.kind
        label = (
            fitting_kind_label(kind) if isinstance(kind, FittingKind) else joint_kind_label(kind)
        )
        lines.append(
            _tr("{kind} an {partner}").format(
                kind=label, partner=doc.boards[connection.partner].name
            )
        )
    return lines


def _polylines(
    painter: QPainter, lines: list[list[P2]], to_page: tuple[float, float, float, float]
) -> None:
    scale, du, dv, bottom = to_page
    for line in lines:
        painter.drawPolyline(
            QPolygonF([QPointF((u - du) * scale, bottom - (v - dv) * scale) for u, v in line])
        )


def paint_picture(
    painter: QPainter,
    k: float,
    picture: StepPicture,
    box: QRectF,
    frame: tuple[P2, P2] | None = None,
) -> None:
    """``picture`` fitted into ``box`` (page mm, ``k`` device units per mm);
    ``frame`` (view mm) keeps the same scale and place for all steps."""
    (u0, v0), (u1, v1) = frame or picture.bounds()
    if u1 - u0 < 1e-6 or v1 - v0 < 1e-6:
        return
    scale = min(box.width() / (u1 - u0), box.height() / (v1 - v0)) * k
    left = box.left() * k + (box.width() * k - (u1 - u0) * scale) / 2
    bottom = box.bottom() * k - (box.height() * k - (v1 - v0) * scale) / 2
    to_page = (scale, u0 - left / scale, v0, bottom)
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    thin = QPen(Qt.GlobalColor.gray, 0.18 * k)
    painter.setPen(thin)
    _polylines(painter, picture.old, to_page)
    dashed = QPen(Qt.GlobalColor.black, 0.25 * k, Qt.PenStyle.DashLine)
    painter.setPen(dashed)
    _polylines(painter, picture.new_hidden, to_page)
    thick = QPen(Qt.GlobalColor.black, 0.6 * k)
    thick.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(thick)
    _polylines(painter, picture.new, to_page)
    painter.restore()


def _font(size: float, bold: bool = False) -> QFont:
    font = QFont("Arial")
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


def write_assembly_pdf(
    path: Path,
    doc: Document,
    evaluation: Evaluation,
    title: str,
    steps: list[AssemblyStep] | None = None,
    pictures: list[StepPicture] | None = None,
) -> int:
    """Write the instructions; returns the number of pages."""
    from simpledesign.core.drawing.assembly import step_pictures

    if steps is None:
        steps = assembly_steps(doc, evaluation)
    if pictures is None:
        pictures = step_pictures(doc, evaluation, steps)
    writer = QPdfWriter(str(path))
    writer.setTitle(_tr("Montageanleitung – {title}").format(title=title))
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
            QPageLayout.Unit.Millimeter,
        )
    )
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(_tr("Die Datei {path} kann nicht geschrieben werden.").format(path=path))
    k = writer.resolution() / 25.4
    pages = 1
    # the finished furniture sets the frame: every step at the same scale
    frame = pictures[-1].bounds() if pictures else None
    try:
        width = PAGE[0] - 2 * MARGIN
        y = MARGIN
        painter.setFont(_font(18, bold=True))
        painter.drawText(
            QRectF(MARGIN * k, y * k, width * k, 10 * k),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _tr("Montageanleitung – {title}").format(title=title),
        )
        y += 12
        painter.setFont(_font(10))
        hardware = [
            f"{item.quantity} × {hardware_label(item)}" for item in evaluation.machining.hardware
        ]
        summary = _tr("{count} Schritte").format(count=len(steps))
        if hardware:
            summary += " · " + _tr("Beschläge: {list}").format(list=", ".join(hardware))
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        bound = painter.boundingRect(QRectF(MARGIN * k, y * k, width * k, 40 * k), flags, summary)
        painter.drawText(bound, flags, summary)
        y += bound.height() / k + 6

        for number, (step, picture) in enumerate(zip(steps, pictures, strict=True), 1):
            if y + STEP_HEIGHT > PAGE[1] - MARGIN + 0.01:
                writer.newPage()
                pages += 1
                y = MARGIN
            box = QRectF(MARGIN * k, y * k, width * k, (STEP_HEIGHT - 4) * k)
            painter.setPen(QPen(Qt.GlobalColor.lightGray, 0.3 * k))
            painter.drawRect(box)
            painter.setPen(Qt.GlobalColor.black)
            painter.setFont(_font(12, bold=True))
            text_box = QRectF((MARGIN + 4) * k, (y + 4) * k, (TEXT_WIDTH - 6) * k, 20 * k)
            title_box = painter.boundingRect(text_box, flags, step_title(doc, number, step))
            painter.drawText(title_box, flags, step_title(doc, number, step))
            painter.setFont(_font(9.5))
            line_y = title_box.bottom() / k + 3
            for line in step_lines(doc, step):
                rect = QRectF((MARGIN + 4) * k, line_y * k, (TEXT_WIDTH - 6) * k, 30 * k)
                used = painter.boundingRect(rect, flags, "• " + line)
                if used.bottom() / k > y + STEP_HEIGHT - 8:
                    break
                painter.drawText(used, flags, "• " + line)
                line_y = used.bottom() / k + 1.5
            picture_box = QRectF(
                MARGIN + TEXT_WIDTH, y + 5, width - TEXT_WIDTH - 5, STEP_HEIGHT - 14
            )
            paint_picture(painter, k, picture, picture_box, frame)
            y += STEP_HEIGHT
    finally:
        painter.end()
    return pages
