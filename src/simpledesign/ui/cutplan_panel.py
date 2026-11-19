"""Cutting plan (Schnittplan): settings, material demand and the drawn sheets/bars.

The drawing is built from "blocks" (a caption, one sheet, one bar …) of known
height. The on-screen view paints them one below the other; the PDF export
uses the same blocks and starts a new page whenever a block does not fit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QMarginsF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPageLayout,
    QPageSize,
    QPainter,
    QPaintEvent,
    QPdfWriter,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.cutlist.optimize import Bar, MaterialPlan, Sheet, optimize
from simpledesign.core.model.settings import CutSettings
from simpledesign.ui.texts import money, number, percent

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
PAPER = QColor("#ffffff")
WASTE = QColor("#d0d0d0")
OUTLINE = QColor("#404040")
WARNING = QColor("#b00020")
GRAIN_BLOCK = QColor("#8a5a00")


def _tr(text: str) -> str:
    return QCoreApplication.translate("CutplanPanel", text)


# ====================================================================== texts
def stock_text(plan: MaterialPlan) -> str:
    m = plan.material
    if not plan.planned or m.stock_length is None:
        return "–"
    if m.is_linear:
        return f"{number(m.stock_length)} mm"
    return f"{number(m.stock_length)} × {number(m.stock_width or 0)} mm"


def count_text(plan: MaterialPlan) -> str:
    if not plan.planned:
        return "–"
    one = plan.stock_count == 1
    if plan.material.is_linear:
        unit = _tr("Stange") if one else _tr("Stangen")
    else:
        unit = _tr("Platte") if one else _tr("Platten")
    return f"{plan.stock_count} {unit}"


def demand_text(plan: MaterialPlan) -> str:
    unit = "m" if plan.material.is_linear else "m²"
    return f"{number(round(plan.demand, 2))} {unit}"


# ====================================================================== drawing
@dataclass(slots=True)
class Block:
    height: float
    draw: Callable[[QPainter, float], None]  # (painter, top)


def _label(painter: QPainter, rect: QRectF, text: str, flags: Qt.AlignmentFlag) -> None:
    metrics = QFontMetricsF(painter.font())
    elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
    painter.drawText(rect, int(flags), elided)


def _caption(text: str, width: float, line: float, bold: bool = False, warn: bool = False) -> Block:
    def draw(painter: QPainter, top: float) -> None:
        painter.save()
        font = QFont(painter.font())
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(WARNING if warn else OUTLINE)
        _label(painter, QRectF(0, top, width, line), text, Qt.AlignmentFlag.AlignVCenter)
        painter.restore()

    return Block(line * (1.4 if bold else 1.0), draw)


def _sheet_block(
    sheet: Sheet, index: int, plan: MaterialPlan, width: float, scale: float, line: float
) -> Block:
    color = QColor(plan.material.color)
    total = len(plan.sheets)
    used = sheet.used_area / sheet.area if sheet.area else 0
    caption = _tr("Platte {i}/{n} – Ausnutzung {used}").format(
        i=index + 1, n=total, used=percent(used)
    )

    def draw(painter: QPainter, top: float) -> None:
        painter.save()
        painter.setPen(OUTLINE)
        _label(painter, QRectF(0, top, width, line), caption, Qt.AlignmentFlag.AlignVCenter)
        y0 = top + line
        outer = QRectF(0, y0, sheet.length * scale, sheet.width * scale)
        painter.setBrush(QBrush(WASTE, Qt.BrushStyle.BDiagPattern))
        painter.drawRect(outer)
        if sheet.trim > 0:
            pen = QPen(OUTLINE)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(outer.adjusted(*(sheet.trim * scale * s for s in (1, 1, -1, -1))))
        painter.setPen(OUTLINE)
        for p in sheet.placements:
            rect = QRectF(p.x * scale, y0 + p.y * scale, p.length * scale, p.width * scale)
            painter.setBrush(color)
            painter.drawRect(rect)
            # Part length and width, independent of how it lies on the sheet.
            length, part_width = (p.width, p.length) if p.rotated else (p.length, p.width)
            text = f"{p.name}\n{number(length)} × {number(part_width)}"
            if p.rotated:
                text += " ↻"
            inner = rect.adjusted(2, 1, -2, -1)
            if inner.height() >= line * 0.9 and inner.width() > line:
                lines = text.split("\n") if inner.height() >= line * 1.8 else [text.split("\n")[0]]
                for k, part in enumerate(lines):
                    row = QRectF(inner.left(), inner.top() + k * line * 0.8, inner.width(), line)
                    _label(painter, row, part, Qt.AlignmentFlag.AlignLeft)
        # grain blocks: one piece, cut apart after the grain was matched
        pen = QPen(GRAIN_BLOCK)
        pen.setWidthF(2.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for b in sheet.blocks:
            painter.drawRect(
                QRectF(b.x * scale, y0 + b.y * scale, b.length * scale, b.width * scale)
            )
        painter.restore()

    return Block(line + sheet.width * scale + line * 0.6, draw)


def _bar_block(
    bar: Bar, index: int, plan: MaterialPlan, width: float, scale: float, line: float
) -> Block:
    color = QColor(plan.material.color)
    height = line * 1.3
    used = bar.used_length / bar.length if bar.length else 0
    caption = _tr("Stange {i}/{n} – Ausnutzung {used}").format(
        i=index + 1, n=len(plan.bars), used=percent(used)
    )

    def draw(painter: QPainter, top: float) -> None:
        painter.save()
        painter.setPen(OUTLINE)
        _label(painter, QRectF(0, top, width, line), caption, Qt.AlignmentFlag.AlignVCenter)
        y0 = top + line
        painter.setBrush(QBrush(WASTE, Qt.BrushStyle.BDiagPattern))
        painter.drawRect(QRectF(0, y0, bar.length * scale, height))
        for cut in bar.cuts:
            rect = QRectF(cut.x * scale, y0, cut.length * scale, height)
            painter.setBrush(color)
            painter.drawRect(rect)
            text = f"{cut.name} {number(cut.length)}"
            _label(painter, rect.adjusted(2, 0, -2, 0), text, Qt.AlignmentFlag.AlignVCenter)
        painter.restore()

    return Block(line + height + line * 0.6, draw)


def plan_blocks(plans: list[MaterialPlan], width: float, line: float) -> list[Block]:
    """All blocks of the drawing for a drawing area ``width`` wide."""
    lengths = [p.material.stock_length for p in plans if p.planned and p.material.stock_length]
    scale = width / max(lengths) if lengths else 1.0
    blocks: list[Block] = []
    for plan in plans:
        title = f"{plan.material.name} – {count_text(plan)}" if plan.planned else plan.material.name
        blocks.append(_caption(title, width, line, bold=True))
        if not plan.planned:
            text = _tr("Kein Rohmaß hinterlegt – Bedarf {demand}").format(demand=demand_text(plan))
            blocks.append(_caption(text, width, line))
        if plan.too_large:
            names = ", ".join(dict.fromkeys(p.name for p in plan.too_large))
            text = _tr("Größer als das Rohmaß: {names}").format(names=names)
            blocks.append(_caption(text, width, line, warn=True))
        for i, sheet in enumerate(plan.sheets):
            blocks.append(_sheet_block(sheet, i, plan, width, scale, line))
        for i, bar in enumerate(plan.bars):
            blocks.append(_bar_block(bar, i, plan, width, scale, line))
    return blocks


def write_pdf(path: Path, plans: list[MaterialPlan], title: str) -> None:
    """The cutting plan as an A4 landscape PDF; a block never spans two pages."""
    writer = QPdfWriter(str(path))
    writer.setTitle(title)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Landscape,
            QMarginsF(12, 12, 12, 12),
            QPageLayout.Unit.Millimeter,
        )
    )
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(_tr("Die Datei {path} kann nicht geschrieben werden.").format(path=path))
    try:
        font = QFont(painter.font())
        font.setPointSizeF(8)
        painter.setFont(font)
        line = QFontMetricsF(font, writer).height() * 1.3
        width, page_height = float(writer.width()), float(writer.height())
        blocks = [_caption(title, width, line, bold=True), *plan_blocks(plans, width, line)]
        y = 0.0
        for block in blocks:
            if y > 0 and y + block.height > page_height:
                writer.newPage()
                y = 0.0
            block.draw(painter, y)
            y += block.height
    finally:
        painter.end()


class PlanView(QWidget):
    """The drawn cutting plans, as wide as the scroll area."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plans: list[MaterialPlan] = []
        self.blocks: list[Block] = []

    def set_plans(self, plans: list[MaterialPlan]) -> None:
        self.plans = plans
        self._layout()

    def _line(self) -> float:
        return QFontMetricsF(self.font()).height() * 1.3

    def _layout(self) -> None:
        self.blocks = plan_blocks(self.plans, max(self.width() - 16, 100), self._line())
        self.setMinimumHeight(int(sum(b.height for b in self.blocks)) + 16)
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        if event.size().width() != event.oldSize().width():
            self._layout()
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        # Always paper-white like the PDF; the drawing colours assume it.
        painter.fillRect(self.rect(), PAPER)
        painter.translate(8, 8)
        y = 0.0
        for block in self.blocks:
            block.draw(painter, y)
            y += block.height
        painter.end()


# ====================================================================== panel
class CutplanPanel(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.plans: list[MaterialPlan] = []
        self._dirty = True
        self._updating = False

        self.kerf_spin = QDoubleSpinBox()
        self.kerf_spin.setRange(0, 20)
        self.kerf_spin.setDecimals(1)
        self.kerf_spin.setSuffix(" mm")
        self.kerf_spin.setToolTip(self.tr("Breite des Sägeschnitts"))
        self.allowance_spin = QDoubleSpinBox()
        self.allowance_spin.setRange(0, 200)
        self.allowance_spin.setDecimals(0)
        self.allowance_spin.setSuffix(" mm")
        self.allowance_spin.setToolTip(self.tr("Zugabe je Teil beim Ablängen von Stangenware"))
        for spin in (self.kerf_spin, self.allowance_spin):
            spin.valueChanged.connect(self._on_settings)
        settings = QFormLayout()
        settings.addRow(self.tr("Schnittbreite"), self.kerf_spin)
        settings.addRow(self.tr("Kappzugabe"), self.allowance_spin)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Material"),
                self.tr("Rohmaß"),
                self.tr("Menge"),
                self.tr("Bedarf netto"),
                self.tr("Ausnutzung"),
                self.tr("Kosten"),
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.total_label = QLabel()
        self.pdf_button = QPushButton(self.tr("Als PDF exportieren…"))
        self.pdf_button.clicked.connect(self.export_pdf)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addLayout(settings)
        left.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.total_label, 1)
        bottom.addWidget(self.pdf_button)
        left.addLayout(bottom)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.view = PlanView()
        self.plan_scroll = QScrollArea()
        self.plan_scroll.setWidgetResizable(True)
        self.plan_scroll.setWidget(self.view)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(self.plan_scroll)
        splitter.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        controller.document_changed.connect(self._invalidate)
        self.refresh()

    # ------------------------------------------------------------------ refresh
    def _invalidate(self) -> None:
        """Optimising takes a moment: only while the panel is visible."""
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        doc = self.controller.doc
        self._updating = True
        try:
            self.kerf_spin.setValue(doc.cut_settings.kerf)
            self.allowance_spin.setValue(doc.cut_settings.crosscut_allowance)
        finally:
            self._updating = False
        self.plans = optimize(doc, self.controller.evaluation)
        self.table.setRowCount(len(self.plans))
        for r, plan in enumerate(self.plans):
            utilisation = plan.utilisation
            cells = (
                plan.material.name,
                stock_text(plan),
                count_text(plan),
                demand_text(plan),
                percent(utilisation) if utilisation is not None else "–",
                money(plan.cost) if plan.cost is not None else "–",
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 2:
                    item.setTextAlignment(RIGHT)
                if c == 0 and plan.too_large:
                    item.setForeground(WARNING)
                    item.setToolTip(self.tr("Einige Teile sind größer als das Rohmaß."))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.total_label.setText(self._total_text())
        self.pdf_button.setEnabled(bool(self.plans))
        self.view.set_plans(self.plans)

    def _total_text(self) -> str:
        costs = [p.cost for p in self.plans]
        known = [c for c in costs if c is not None]
        if not known:
            return self.tr("Kosten: keine Preise hinterlegt")
        text = self.tr("Materialkosten: {total}").format(total=money(sum(known)))
        if len(known) < len(costs):
            text += " " + self.tr("(ohne Materialien ohne Preis)")
        return text

    # ------------------------------------------------------------------ edits
    def _on_settings(self) -> None:
        if self._updating:
            return
        settings = CutSettings(self.kerf_spin.value(), self.allowance_spin.value())
        if settings != self.controller.doc.cut_settings:
            self.controller.set_cut_settings(settings)

    def export_pdf(self) -> None:
        file_path = self.controller.file_path
        start = file_path.with_name(file_path.stem + "_Schnittplan.pdf") if file_path else ""
        path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("Schnittplan exportieren"), str(start), self.tr("PDF-Datei (*.pdf)")
        )
        if not path:
            return
        title = self.tr("Schnittplan – {name}").format(
            name=file_path.stem if file_path else self.tr("Unbenannt")
        )
        try:
            write_pdf(Path(path), self.plans, title)
        except OSError as error:
            QMessageBox.critical(self, self.tr("Export"), str(error))
