"""Technical drawings: preview of the sheets and export as PDF or DXF."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QMarginsF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPageLayout,
    QPageSize,
    QPainter,
    QPaintEvent,
    QPdfWriter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.drawing.layout import DrawingLabels, DrawingOptions, build_sheets
from simpledesign.core.drawing.sheet import Align, PaperSize, Polyline, Sheet, Style, Text, expand
from simpledesign.core.io.dxf import write_dxf


def _tr(text: str) -> str:
    return QCoreApplication.translate("DrawingDialog", text)


def drawing_labels() -> DrawingLabels:
    return DrawingLabels(
        overview=_tr("Übersicht"),
        scale=_tr("Maßstab"),
        date=_tr("Datum"),
        sheet=_tr("Blatt"),
        sheet_of=_tr("{n} von {m}"),
        material=_tr("Material"),
        quantity=_tr("Anzahl"),
        size=_tr("Maß L × B × D"),
        parts=_tr("Teile"),
        holes=_tr("Bohrungen"),
        hole_columns=(_tr("Nr"), _tr("X"), _tr("Y"), _tr("Z"), _tr("Ø"), _tr("Tiefe")),
        edges=_tr("Kanten"),
    )


# Pen widths in paper mm.
WIDTHS = {Style.VISIBLE: 0.5, Style.HIDDEN: 0.25, Style.THIN: 0.18, Style.FRAME: 0.5}


def paint_sheet(painter: QPainter, sheet: Sheet, target: QRectF) -> None:
    """Draw ``sheet`` into ``target`` (device units), keeping the aspect ratio."""
    w, h = sheet.size
    k = min(target.width() / w, target.height() / h)
    ox = target.x() + (target.width() - w * k) / 2
    oy = target.y() + (target.height() - h * k) / 2

    def pt(p: tuple[float, float]) -> QPointF:
        return QPointF(ox + p[0] * k, oy + (h - p[1]) * k)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(QRectF(ox, oy, w * k, h * k), QColor("white"))
    pens: dict[Style, QPen] = {}
    for style, width in WIDTHS.items():
        pen = QPen(QColor("black"), max(width * k, 0.5))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if style == Style.HIDDEN:
            pen.setColor(QColor(90, 90, 90))
            pen.setDashPattern([3.0 / max(width, 1e-3), 1.5 / max(width, 1e-3)])
        pens[style] = pen
    painter.setBrush(Qt.BrushStyle.NoBrush)
    font = QFont("Arial")
    for item in sheet.items:
        for prim in expand(item):
            if isinstance(prim, Polyline):
                painter.setPen(pens[prim.style])
                poly = QPolygonF([pt(p) for p in prim.points])
                if prim.closed:
                    painter.drawPolygon(poly)
                else:
                    painter.drawPolyline(poly)
            else:
                _draw_text(painter, prim, pt(prim.pos), k, font)
    painter.restore()


def _draw_text(painter: QPainter, text: Text, at: QPointF, k: float, font: QFont) -> None:
    # Cap height ~ 0.7 of the font's pixel size.
    pixel = text.height * k / 0.72
    if pixel < 1.0:
        return
    f = QFont(font)
    f.setPixelSize(max(1, round(pixel)))
    f.setBold(text.bold)
    painter.setFont(f)
    painter.setPen(QColor("black"))
    width = QFontMetricsF(f).horizontalAdvance(text.text)
    dx = {Align.LEFT: 0.0, Align.CENTER: -width / 2, Align.RIGHT: -width}[text.align]
    painter.save()
    painter.translate(at)
    painter.rotate(-text.rotation)
    painter.drawText(QPointF(dx, 0.0), text.text)
    painter.restore()


def write_pdf(sheets: list[Sheet], path: Path, title: str) -> None:
    """One page per sheet, at true scale."""
    if not sheets:
        return
    writer = QPdfWriter(str(path))
    writer.setTitle(title)
    writer.setResolution(600)

    def layout_of(sheet: Sheet) -> QPageLayout:
        w, h = sheet.size
        size = QPageSize(QSizeF(w, h), QPageSize.Unit.Millimeter, sheet.paper.value)
        return QPageLayout(
            size,
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
            QPageLayout.Unit.Millimeter,
        )

    writer.setPageLayout(layout_of(sheets[0]))
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(_tr("Die Datei {path} kann nicht geschrieben werden.").format(path=path))
    try:
        for i, sheet in enumerate(sheets):
            if i:
                writer.setPageLayout(layout_of(sheet))
                writer.newPage()
            target = QRectF(0, 0, writer.width(), writer.height())
            paint_sheet(painter, sheet, target)
    finally:
        painter.end()


class SheetView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sheet: Sheet | None = None
        self.setMinimumSize(560, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_sheet(self, sheet: Sheet | None) -> None:
        self.sheet = sheet
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        if self.sheet is not None:
            paint_sheet(painter, self.sheet, QRectF(self.rect()).adjusted(8, 8, -8, -8))
        painter.end()


class DrawingDialog(QDialog):
    """Options, sheet preview with paging, export buttons."""

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(_tr("Zeichnung"))
        self.sheets: list[Sheet] = []
        self.page = 0

        self.paper_combo = QComboBox()
        for paper in PaperSize:
            self.paper_combo.addItem(_tr("{paper} quer").format(paper=paper.value), paper)
        self.overview_check = QCheckBox(_tr("Übersicht"))
        self.overview_check.setChecked(True)
        self.parts_check = QCheckBox(_tr("Einzelteile"))
        self.parts_check.setChecked(True)
        self.hidden_check = QCheckBox(_tr("Verdeckte Kanten"))
        self.hidden_check.setChecked(True)
        options = QHBoxLayout()
        options.addWidget(QLabel(_tr("Format:")))
        options.addWidget(self.paper_combo)
        for check in (self.overview_check, self.parts_check, self.hidden_check):
            options.addWidget(check)
        options.addStretch()

        self.view = SheetView()
        self.prev_button = QPushButton("◀")
        self.next_button = QPushButton("▶")
        self.page_label = QLabel()
        nav = QHBoxLayout()
        nav.addStretch()
        nav.addWidget(self.prev_button)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_button)
        nav.addStretch()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.pdf_button = self.buttons.addButton(
            _tr("Als PDF…"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.dxf_button = self.buttons.addButton(
            _tr("Als DXF…"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.buttons.rejected.connect(self.reject)
        self.pdf_button.clicked.connect(self.export_pdf)
        self.dxf_button.clicked.connect(self.export_dxf)
        self.prev_button.clicked.connect(lambda: self.show_page(self.page - 1))
        self.next_button.clicked.connect(lambda: self.show_page(self.page + 1))
        self.paper_combo.currentIndexChanged.connect(self.rebuild)
        for check in (self.overview_check, self.parts_check, self.hidden_check):
            check.toggled.connect(self.rebuild)

        layout = QVBoxLayout(self)
        layout.addLayout(options)
        layout.addWidget(self.view, 1)
        layout.addLayout(nav)
        layout.addWidget(self.buttons)
        self.resize(1000, 760)
        self.rebuild()

    @property
    def title(self) -> str:
        path = self.controller.file_path
        return path.stem if path else _tr("Unbenannt")

    def options(self) -> DrawingOptions:
        return DrawingOptions(
            paper=PaperSize(self.paper_combo.currentData()),
            overview=self.overview_check.isChecked(),
            parts=self.parts_check.isChecked(),
            hidden_lines=self.hidden_check.isChecked(),
        )

    def rebuild(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.sheets = build_sheets(
                self.controller.doc,
                self.controller.evaluation,
                drawing_labels(),
                self.options(),
                self.title,
                date.today().strftime("%d.%m.%Y"),
            )
        finally:
            QApplication.restoreOverrideCursor()
        self.show_page(min(self.page, max(len(self.sheets) - 1, 0)))

    def show_page(self, page: int) -> None:
        count = len(self.sheets)
        self.page = max(0, min(page, count - 1)) if count else 0
        self.view.set_sheet(self.sheets[self.page] if count else None)
        self.page_label.setText(
            _tr("Blatt {n} von {m}").format(n=self.page + 1, m=count) if count else _tr("Leer")
        )
        self.prev_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page < count - 1)
        self.pdf_button.setEnabled(bool(count))
        self.dxf_button.setEnabled(bool(count))

    def _start_path(self, suffix: str) -> str:
        path = self.controller.file_path
        return str(path.with_name(path.stem + "_Zeichnung" + suffix)) if path else ""

    def export_pdf(self) -> None:
        path, _f = QFileDialog.getSaveFileName(
            self, _tr("Zeichnung als PDF"), self._start_path(".pdf"), _tr("PDF-Datei (*.pdf)")
        )
        if path:
            self.save(Path(path))

    def export_dxf(self) -> None:
        path, _f = QFileDialog.getSaveFileName(
            self, _tr("Zeichnung als DXF"), self._start_path(".dxf"), _tr("DXF-Datei (*.dxf)")
        )
        if path:
            self.save(Path(path))

    def save(self, path: Path) -> bool:
        try:
            if path.suffix.lower() == ".dxf":
                write_dxf(self.sheets, path)
            else:
                write_pdf(self.sheets, path, self.title)
        except OSError as error:
            QMessageBox.critical(self, _tr("Zeichnung"), str(error))
            return False
        return True
