"""Part labels (Etiketten) as a PDF for A4 label sheets, with a page preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QMarginsF, QPointF, QRectF, QSettings, Qt
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
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.cutlist.cutlist import edges_summary
from simpledesign.core.cutlist.labels import (
    A4,
    LABEL_SHEETS,
    LabelSheet,
    PartLabel,
    build_labels,
    label_sheet,
    qr_matrix,
)
from simpledesign.core.materials.edgeband import BandEdge
from simpledesign.ui.texts import number

SHEET_SETTING = "labels/sheet"


def _tr(text: str) -> str:
    return QCoreApplication.translate("Labels", text)


# ====================================================================== painting
def _qr(painter: QPainter, rect: QRectF, text: str) -> None:
    matrix = qr_matrix(text)
    modules = len(matrix) + 4  # two modules of quiet zone around
    cell = rect.width() / modules
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("black")))
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                # a hair larger, so neighbouring modules leave no gap
                painter.drawRect(
                    QRectF(
                        rect.left() + (c + 2) * cell,
                        rect.top() + (r + 2) * cell,
                        cell * 1.02,
                        cell * 1.02,
                    )
                )
    painter.restore()


def _sketch(painter: QPainter, area: QRectF, label: PartLabel, k: float) -> None:
    """The part from above (length to the right): banded edges thick, grain arrow."""
    length, width = label.finished[0], label.finished[1]
    if area.width() <= 0 or area.height() <= 0 or length <= 0 or width <= 0:
        return
    ratio = max(width / length, 0.3)  # very narrow parts stay readable
    w = min(area.width(), area.height() / ratio)
    h = w * ratio
    rect = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("black"), 0.15 * k))
    painter.drawRect(rect)
    thick = QPen(QColor("black"), 0.7 * k)
    thick.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(thick)
    inset = 0.35 * k
    lines = {
        BandEdge.L1: (rect.bottomLeft(), rect.bottomRight(), QPointF(0, inset)),
        BandEdge.L2: (rect.topLeft(), rect.topRight(), QPointF(0, -inset)),
        BandEdge.W1: (rect.topLeft(), rect.bottomLeft(), QPointF(-inset, 0)),
        BandEdge.W2: (rect.topRight(), rect.bottomRight(), QPointF(inset, 0)),
    }
    for edge, _band in label.edges:
        a, b, offset = lines[edge]
        painter.drawLine(a + offset, b + offset)
    if label.has_grain:
        painter.setPen(QPen(QColor("black"), 0.2 * k))
        y = rect.center().y()
        left, right = rect.left() + rect.width() * 0.15, rect.right() - rect.width() * 0.15
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        head = min(rect.height() * 0.3, 1.2 * k)
        painter.setBrush(QBrush(QColor("black")))
        for tip, sign in ((right, -1), (left, 1)):
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(tip, y),
                        QPointF(tip + sign * head, y - head / 2),
                        QPointF(tip + sign * head, y + head / 2),
                    ]
                )
            )
    painter.restore()


def label_lines(label: PartLabel, short_edges: bool = False) -> list[str]:
    """The text lines of a label (the first one is printed bold); with
    ``short_edges`` only the codes of the banded edges."""
    lines = [
        _tr("{pos}  {name}").format(
            pos=_tr("Pos. {position} ({copy}/{copies})").format(
                position=label.position, copy=label.copy, copies=label.copies
            ),
            name=label.name,
        ),
        label.furniture,
        _tr("Fertig: {size} mm").format(size=label.size_text(label.finished, number)),
        _tr("Roh: {size} mm").format(size=label.size_text(label.rough, number)),
        label.material + (_tr(", Faser ↔") if label.has_grain else ""),
    ]
    if label.edges:
        if short_edges:
            edges = ", ".join(edge.value.upper() for edge, _band in label.edges)
        else:
            edges = edges_summary(label.edges)
        lines.append(_tr("Kanten: {edges}").format(edges=edges))
    return [line for line in lines if line]


def paint_label(
    painter: QPainter, origin: QPointF, k: float, sheet: LabelSheet, label: PartLabel
) -> None:
    """One label with its top left corner at ``origin``; ``k`` = device units per mm."""
    w, h = sheet.width * k, sheet.height * k
    pad = min(2.0, sheet.height * 0.06) * k
    inner = h - 2 * pad
    qr = min(w * 0.32, inner * 0.7)
    qr_rect = QRectF(origin.x() + w - pad - qr, origin.y() + pad, qr, qr)
    _qr(painter, qr_rect, label.qr_text(number))
    sketch = QRectF(
        qr_rect.left() + qr * 0.1,
        qr_rect.bottom() + 0.8 * k,
        qr * 0.8,
        origin.y() + h - pad - qr_rect.bottom() - 1.2 * k,
    )
    _sketch(painter, sketch, label, k)

    lines = label_lines(label)
    text_width = qr_rect.left() - origin.x() - 2 * pad
    line = min(inner / max(len(lines), 5), 4.2 * k)
    font = QFont(painter.font())
    font.setPixelSize(max(1, round(line * 0.72)))
    painter.save()
    painter.setPen(QColor("black"))
    for i, text in enumerate(lines):
        font.setBold(i == 0)
        painter.setFont(font)
        metrics = QFontMetricsF(font, painter.device())
        if label.edges and i == len(lines) - 1 and metrics.horizontalAdvance(text) > text_width:
            text = label_lines(label, short_edges=True)[-1]
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, text_width)
        rect = QRectF(origin.x() + pad, origin.y() + pad + i * line, text_width, line)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
    painter.restore()


def paint_page(
    painter: QPainter,
    k: float,
    sheet: LabelSheet,
    labels: list[tuple[int, PartLabel]],
    frames: bool,
) -> None:
    """Labels at their slots (index on the sheet) of one page."""
    for slot, label in labels:
        x, y = sheet.position(slot)
        origin = QPointF(x * k, y * k)
        if frames:
            painter.save()
            painter.setPen(QPen(QColor("#999999"), 0.1 * k))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(origin.x(), origin.y(), sheet.width * k, sheet.height * k))
            painter.restore()
        paint_label(painter, origin, k, sheet, label)


def pages(
    sheet: LabelSheet, labels: list[PartLabel], skip: int
) -> list[list[tuple[int, PartLabel]]]:
    """Labels per page with their slot; the first ``skip`` slots stay empty."""
    result: list[list[tuple[int, PartLabel]]] = []
    for i, label in enumerate(labels):
        slot = skip + i
        if slot % sheet.per_sheet == 0 or not result:
            result.append([])
        result[-1].append((slot % sheet.per_sheet, label))
    return result


def write_labels_pdf(
    path: Path, labels: list[PartLabel], sheet: LabelSheet, skip: int = 0, frames: bool = False
) -> None:
    writer = QPdfWriter(str(path))
    writer.setTitle(_tr("Etiketten"))
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
    try:
        k = writer.resolution() / 25.4
        for i, page in enumerate(pages(sheet, labels, skip)):
            if i:
                writer.newPage()
            paint_page(painter, k, sheet, page, frames)
    finally:
        painter.end()


# ====================================================================== dialog
class SheetPreview(QWidget):
    """The first page, scaled to the widget, with the label outlines."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sheet = LABEL_SHEETS[0]
        self.page: list[tuple[int, PartLabel]] = []
        self.setMinimumSize(250, 354)

    def set_page(self, sheet: LabelSheet, page: list[tuple[int, PartLabel]]) -> None:
        self.sheet, self.page = sheet, page
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = min(self.width() / A4[0], self.height() / A4[1])
        painter.translate((self.width() - A4[0] * k) / 2, (self.height() - A4[1] * k) / 2)
        painter.fillRect(QRectF(0, 0, A4[0] * k, A4[1] * k), QColor("white"))
        painter.setPen(QPen(QColor("#dddddd"), 1))
        for slot in range(self.sheet.per_sheet):
            x, y = self.sheet.position(slot)
            painter.drawRect(QRectF(x * k, y * k, self.sheet.width * k, self.sheet.height * k))
        paint_page(painter, k, self.sheet, self.page, frames=False)
        painter.end()


class LabelsDialog(QDialog):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(self.tr("Etiketten"))
        self.labels: list[PartLabel] = []

        self.sheet_combo = QComboBox()
        for sheet in LABEL_SHEETS:
            self.sheet_combo.addItem(_tr(sheet.name), sheet.id)
        saved = str(QSettings().value(SHEET_SETTING, LABEL_SHEETS[0].id))
        self.sheet_combo.setCurrentIndex(max(0, self.sheet_combo.findData(saved)))
        self.skip_spin = QSpinBox()
        self.skip_spin.setToolTip(
            self.tr("Bei einem angebrochenen Bogen: Nummer des ersten freien Etiketts")
        )
        self.selected_check = QCheckBox(self.tr("Nur ausgewählte Teile"))
        self.selected_check.setEnabled(bool(self._selected()))
        self.selected_check.setChecked(bool(self._selected()))
        self.frames_check = QCheckBox(self.tr("Rahmen drucken (für Normalpapier)"))
        self.count_label = QLabel()
        self.preview = SheetPreview()

        form = QFormLayout()
        form.addRow(self.tr("Etikettenbogen:"), self.sheet_combo)
        form.addRow(self.tr("Erstes Etikett:"), self.skip_spin)
        form.addRow(self.selected_check)
        form.addRow(self.frames_check)
        form.addRow(self.count_label)

        self.export_button = QPushButton(self.tr("Als PDF speichern…"))
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self.export_pdf)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.export_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addStretch(1)
        row = QHBoxLayout()
        row.addLayout(left)
        row.addWidget(self.preview, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(row, 1)
        layout.addWidget(buttons)

        self.sheet_combo.currentIndexChanged.connect(self._on_sheet)
        self.skip_spin.valueChanged.connect(self._update)
        self.selected_check.toggled.connect(self._rebuild)
        self._on_sheet()

    # ------------------------------------------------------------------ state
    def sheet(self) -> LabelSheet:
        return label_sheet(str(self.sheet_combo.currentData()))

    def title(self) -> str:
        path = self.controller.file_path
        return path.stem if path else self.tr("Unbenannt")

    def _selected(self) -> set[str]:
        return {i for i in self.controller.selection if i in self.controller.doc.boards}

    def _on_sheet(self) -> None:
        sheet = self.sheet()
        QSettings().setValue(SHEET_SETTING, sheet.id)
        self.skip_spin.setRange(1, sheet.per_sheet)
        self._rebuild()

    def _rebuild(self) -> None:
        only = self._selected() if self.selected_check.isChecked() else None
        c = self.controller
        self.labels = build_labels(c.doc, c.evaluation, self.title(), only)
        self._update()

    def _update(self) -> None:
        sheet = self.sheet()
        result = pages(sheet, self.labels, self.skip_spin.value() - 1)
        self.count_label.setText(
            self.tr("{n} Etiketten auf {pages} Bögen").format(n=len(self.labels), pages=len(result))
        )
        self.preview.set_page(sheet, result[0] if result else [])
        self.export_button.setEnabled(bool(self.labels))

    # ------------------------------------------------------------------ export
    def export_to(self, path: Path) -> bool:
        try:
            write_labels_pdf(
                path,
                self.labels,
                self.sheet(),
                self.skip_spin.value() - 1,
                self.frames_check.isChecked(),
            )
        except OSError as error:
            QMessageBox.critical(self, self.tr("Etiketten"), str(error))
            return False
        return True

    def export_pdf(self) -> None:
        file_path = self.controller.file_path
        start = file_path.with_name(file_path.stem + "_Etiketten.pdf") if file_path else ""
        path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("Etiketten speichern"), str(start), self.tr("PDF-Datei (*.pdf)")
        )
        if path:
            self.export_to(Path(path))
