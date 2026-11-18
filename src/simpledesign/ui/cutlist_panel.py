"""Cut list table with CSV export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.cutlist.cutlist import CutRow, build_cutlist, to_csv
from simpledesign.ui.texts import number

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class CutlistPanel(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.rows: list[CutRow] = []

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(self.headers())
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self.summary = QLabel()
        self.export_action = QAction(self.tr("Zuschnittliste als CSV exportieren…"), self)
        self.export_action.triggered.connect(self.export_csv)
        self.export_button = QPushButton(self.tr("Als CSV exportieren…"))
        self.export_button.clicked.connect(self.export_csv)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary)
        bottom.addStretch(1)
        bottom.addWidget(self.export_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(bottom)

        controller.document_changed.connect(self.refresh)
        self.refresh()

    def headers(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.tr("Anzahl"),
            self.tr("Länge"),
            self.tr("Breite"),
            self.tr("Dicke"),
            self.tr("Material"),
            self.tr("Teile"),
        )

    def refresh(self) -> None:
        self.rows = build_cutlist(self.controller.doc, self.controller.evaluation)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            cells = (
                str(row.quantity),
                number(row.length),
                number(row.width),
                number(row.thickness),
                row.material,
                ", ".join(row.names),
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c < 4:
                    item.setTextAlignment(RIGHT)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        total = sum(row.quantity for row in self.rows)
        self.summary.setText(self.tr("{n} Teile").format(n=total))
        self.export_button.setEnabled(bool(self.rows))
        self.export_action.setEnabled(bool(self.rows))

    def export_csv(self) -> None:
        start = self.controller.file_path.with_suffix(".csv") if self.controller.file_path else ""
        path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("Zuschnittliste exportieren"), str(start), self.tr("CSV-Datei (*.csv)")
        )
        if not path:
            return
        try:
            # utf-8-sig: Excel only detects UTF-8 (umlauts) with a BOM.
            Path(path).write_text(
                to_csv(self.rows, self.headers()), encoding="utf-8-sig", newline=""
            )
        except OSError as error:
            QMessageBox.critical(self, self.tr("Export"), str(error))
