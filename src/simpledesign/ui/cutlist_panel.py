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
from simpledesign.ui.texts import hardware_label, machining_text, number

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class CutlistPanel(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.rows: list[CutRow] = []

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(self.headers())
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        # Fittings (Beschläge) from the joints.
        self.hardware_label = QLabel(self.tr("Beschläge"))
        self.hardware_table = QTableWidget(0, 2)
        self.hardware_table.setHorizontalHeaderLabels([self.tr("Anzahl"), self.tr("Beschlag")])
        self.hardware_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hardware_table.verticalHeader().setVisible(False)
        self.hardware_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.hardware_table.setMaximumHeight(120)

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
        layout.addWidget(self.hardware_label)
        layout.addWidget(self.hardware_table)
        layout.addLayout(bottom)

        controller.document_changed.connect(self.refresh)
        self.refresh()

    def headers(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            self.tr("Anzahl"),
            self.tr("Länge"),
            self.tr("Breite"),
            self.tr("Dicke"),
            self.tr("Material"),
            self.tr("Bearbeitung"),
            self.tr("Teile"),
        )

    @staticmethod
    def machining(row: CutRow) -> str:
        return machining_text(row.holes, row.cutouts)

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
                self.machining(row),
                ", ".join(row.names),
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c < 4:
                    item.setTextAlignment(RIGHT)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        hardware = self.controller.evaluation.machining.hardware
        self.hardware_table.setRowCount(len(hardware))
        for r, fitting in enumerate(hardware):
            quantity = QTableWidgetItem(str(fitting.quantity))
            quantity.setTextAlignment(RIGHT)
            self.hardware_table.setItem(r, 0, quantity)
            self.hardware_table.setItem(r, 1, QTableWidgetItem(hardware_label(fitting)))
        self.hardware_label.setVisible(bool(hardware))
        self.hardware_table.setVisible(bool(hardware))
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
                to_csv(self.rows, self.headers(), self.machining), encoding="utf-8-sig", newline=""
            )
        except OSError as error:
            QMessageBox.critical(self, self.tr("Export"), str(error))
