"""Linear array: copies of the selection in a row ("Reihe kopieren")."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

Vec3 = tuple[float, float, float]


class ArrayDialog(QDialog):
    """Number of copies, direction and spacing (centre to centre).

    ``extent``: size of the selection per axis - the default spacing puts the
    copies right next to each other.
    """

    def __init__(self, extent: Vec3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Reihe kopieren"))
        self._extent = extent
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(3)
        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["X", "Y", "Z"])
        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(-100000, 100000)
        self.spacing_spin.setDecimals(1)
        self.spacing_spin.setSuffix(" mm")
        self.axis_combo.currentIndexChanged.connect(self._default_spacing)
        self._default_spacing(0)
        form = QFormLayout()
        form.addRow(self.tr("Anzahl Kopien"), self.count_spin)
        form.addRow(self.tr("Richtung"), self.axis_combo)
        form.addRow(self.tr("Abstand (Mitte zu Mitte)"), self.spacing_spin)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _default_spacing(self, axis: int) -> None:
        self.spacing_spin.setValue(self._extent[axis])

    def step(self) -> Vec3:
        """Offset from one copy to the next."""
        step = [0.0, 0.0, 0.0]
        step[self.axis_combo.currentIndex()] = self.spacing_spin.value()
        return step[0], step[1], step[2]
