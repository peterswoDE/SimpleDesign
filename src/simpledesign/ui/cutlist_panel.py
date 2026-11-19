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
from simpledesign.core.cutlist.cutlist import (
    CutRow,
    band_demand,
    build_cutlist,
    edges_summary,
    to_csv,
)
from simpledesign.ui.texts import hardware_label, machining_text, number

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class CutlistPanel(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.rows: list[CutRow] = []

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(self.headers())
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        edges_header = self.table.horizontalHeaderItem(6)
        assert edges_header is not None
        edges_header.setToolTip(
            self.tr(
                "Umleimer: L1/L2 = Längskanten, W1/W2 = Querkanten. "
                "Länge und Breite sind schon um den Umleimer gekürzt (Rohmaß)."
            )
        )

        # Edge banding: running metres per kind.
        self.band_label = QLabel(self.tr("Umleimer"))
        self.band_table = QTableWidget(0, 3)
        self.band_table.setHorizontalHeaderLabels(
            [self.tr("Laufmeter"), self.tr("Kanten"), self.tr("Umleimer")]
        )
        self.band_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.band_table.verticalHeader().setVisible(False)
        self.band_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.band_table.setToolTip(self.tr("Je Kante 30 mm Überstand eingerechnet"))
        self.band_table.setMaximumHeight(120)

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
        layout.addWidget(self.band_label)
        layout.addWidget(self.band_table)
        layout.addWidget(self.hardware_label)
        layout.addWidget(self.hardware_table)
        layout.addLayout(bottom)

        controller.document_changed.connect(self.refresh)
        self.refresh()

    def headers(self) -> tuple[str, str, str, str, str, str, str, str]:
        return (
            self.tr("Anzahl"),
            self.tr("Länge"),
            self.tr("Breite"),
            self.tr("Dicke"),
            self.tr("Material"),
            self.tr("Bearbeitung"),
            self.tr("Kanten"),
            self.tr("Teile"),
        )

    @staticmethod
    def machining(row: CutRow) -> str:
        return machining_text(row.holes, row.cutouts)

    @staticmethod
    def edges(row: CutRow) -> str:
        return edges_summary(row.edges)

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
                self.edges(row),
                ", ".join(row.names),
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c < 4:
                    item.setTextAlignment(RIGHT)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        demand = band_demand(self.controller.doc, self.controller.evaluation)
        self.band_table.setRowCount(len(demand))
        for r, band in enumerate(demand):
            metres = QTableWidgetItem(number(round(band.length / 1000, 2)))
            metres.setTextAlignment(RIGHT)
            count = QTableWidgetItem(str(band.edges))
            count.setTextAlignment(RIGHT)
            self.band_table.setItem(r, 0, metres)
            self.band_table.setItem(r, 1, count)
            self.band_table.setItem(r, 2, QTableWidgetItem(band.name))
        self.band_label.setVisible(bool(demand))
        self.band_table.setVisible(bool(demand))
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
                to_csv(self.rows, self.headers(), self.machining, self.edges),
                encoding="utf-8-sig",
                newline="",
            )
        except OSError as error:
            QMessageBox.critical(self, self.tr("Export"), str(error))
