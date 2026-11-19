"""Dialog for an own edge banding (name, thickness, colour)."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from simpledesign.core.ids import new_id
from simpledesign.core.materials.edgeband import EdgeBand


class EdgeBandDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Eigener Umleimer"))
        form = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("z. B. ABS 1 mm grau"))
        form.addRow(self.tr("Name"), self.name_edit)
        self.thickness_spin = QDoubleSpinBox()
        self.thickness_spin.setRange(0.1, 50)
        self.thickness_spin.setDecimals(1)
        self.thickness_spin.setValue(1.0)
        self.thickness_spin.setSuffix(" mm")
        self.thickness_spin.setToolTip(self.tr("Wird beim Zuschnitt vom Fertigmaß abgezogen"))
        form.addRow(self.tr("Dicke"), self.thickness_spin)
        self.color = QColor("#f0f0f0")
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._pick_color)
        self._show_color()
        form.addRow(self.tr("Farbe"), self.color_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.name_edit.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(False)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self.color, self, self.tr("Farbe des Umleimers"))
        if color.isValid():
            self.color = color
            self._show_color()

    def _show_color(self) -> None:
        self.color_button.setText(self.color.name())
        self.color_button.setStyleSheet(f"background: {self.color.name()};")

    def band(self) -> EdgeBand:
        return EdgeBand(
            new_id(),
            self.name_edit.text().strip(),
            self.thickness_spin.value(),
            self.color.name(),
        )
