"""Groove settings for back panels ("In Nut setzen")."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from simpledesign.core.model.fit import GrooveSettings


class GrooveDialog(QDialog):
    """Depth, play and distance from the back edge of the groove a panel sits in."""

    def __init__(self, settings: GrooveSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("In Nut setzen"))
        info = QLabel(
            self.tr(
                "Die Platte wird zwischen die umgebenden Teile eingepasst, um die "
                "Nuttiefe vergrößert und mit Nuten verbunden."
            )
        )
        info.setWordWrap(True)
        self.depth_spin = self._spin(settings.depth, 0.5, 50)
        self.play_spin = self._spin(settings.play, 0.0, 10)
        self.distance_spin = self._spin(settings.distance, 0.0, 500)
        form = QFormLayout()
        form.addRow(self.tr("Nuttiefe"), self.depth_spin)
        form.addRow(self.tr("Luft im Nutgrund"), self.play_spin)
        form.addRow(self.tr("Abstand zur Hinterkante"), self.distance_spin)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _spin(value: float, low: float, high: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix(" mm")
        spin.setValue(value)
        return spin

    def settings(self) -> GrooveSettings:
        return GrooveSettings(
            depth=self.depth_spin.value(),
            play=self.play_spin.value(),
            distance=self.distance_spin.value(),
        )
