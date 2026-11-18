"""User preferences (QSettings) and the shortcut editor."""

from __future__ import annotations

from dataclasses import dataclass, fields

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QKeySequenceEdit,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _tr(text: str) -> str:
    return QCoreApplication.translate("SettingsDialog", text)


@dataclass(slots=True)
class Preferences:
    snapping: bool = True
    snap_tolerance_px: int = 8
    autosave_minutes: int = 2
    board_length: float = 600.0
    board_width: float = 300.0
    board_thickness: float = 18.0

    @classmethod
    def load(cls) -> Preferences:
        settings = QSettings()
        prefs = cls()
        for f in fields(cls):
            default = getattr(prefs, f.name)
            raw = settings.value(f"preferences/{f.name}", default)
            try:
                if isinstance(default, bool):
                    value: object = raw if isinstance(raw, bool) else str(raw).lower() == "true"
                else:
                    value = type(default)(raw)
            except TypeError, ValueError:
                value = default
            setattr(prefs, f.name, value)
        return prefs

    def save(self) -> None:
        settings = QSettings()
        for f in fields(self):
            settings.setValue(f"preferences/{f.name}", getattr(self, f.name))


class SettingsDialog(QDialog):
    def __init__(self, prefs: Preferences, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Einstellungen"))

        self.snap_check = QCheckBox(_tr("An Kanten einrasten"))
        self.snap_check.setChecked(prefs.snapping)
        self.snap_spin = QSpinBox()
        self.snap_spin.setRange(1, 50)
        self.snap_spin.setSuffix(" px")
        self.snap_spin.setValue(prefs.snap_tolerance_px)
        snap_box = QGroupBox(_tr("Einrasten"))
        snap_form = QFormLayout(snap_box)
        snap_form.addRow(self.snap_check)
        snap_form.addRow(_tr("Fangabstand"), self.snap_spin)

        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(1, 60)
        self.autosave_spin.setSuffix(" min")
        self.autosave_spin.setValue(prefs.autosave_minutes)
        save_box = QGroupBox(_tr("Sicherung"))
        save_form = QFormLayout(save_box)
        save_form.addRow(_tr("Automatisch sichern alle"), self.autosave_spin)

        self.size_spins: list[QDoubleSpinBox] = []
        board_box = QGroupBox(_tr("Neues Brett"))
        board_form = QFormLayout(board_box)
        for label, value in (
            (_tr("Länge"), prefs.board_length),
            (_tr("Breite"), prefs.board_width),
            (_tr("Dicke"), prefs.board_thickness),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(1, 10_000)
            spin.setDecimals(1)
            spin.setSuffix(" mm")
            spin.setValue(value)
            board_form.addRow(label, spin)
            self.size_spins.append(spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for box in (snap_box, save_box, board_box):
            layout.addWidget(box)
        layout.addWidget(buttons)

    def preferences(self) -> Preferences:
        length, width, thickness = (spin.value() for spin in self.size_spins)
        return Preferences(
            snapping=self.snap_check.isChecked(),
            snap_tolerance_px=self.snap_spin.value(),
            autosave_minutes=self.autosave_spin.value(),
            board_length=length,
            board_width=width,
            board_thickness=thickness,
        )


# ---------------------------------------------------------------- shortcuts
SHORTCUT_KEY = "shortcuts/{name}"


def apply_saved_shortcuts(actions: dict[str, QAction]) -> None:
    settings = QSettings()
    for name, action in actions.items():
        saved = settings.value(SHORTCUT_KEY.format(name=name))
        if saved is not None:
            action.setShortcut(QKeySequence(str(saved)))


class ShortcutsDialog(QDialog):
    """All actions with their shortcuts; a cell edit assigns a new one."""

    def __init__(
        self,
        actions: dict[str, QAction],
        defaults: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Tastenkürzel"))
        self.named = actions
        self.defaults = defaults
        self.names = sorted(actions, key=lambda n: actions[n].text().replace("&", ""))

        self.table = QTableWidget(len(self.names), 2)
        self.table.setHorizontalHeaderLabels([_tr("Befehl"), _tr("Tastenkürzel")])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.editors: dict[str, QKeySequenceEdit] = {}
        for row, name in enumerate(self.names):
            action = actions[name]
            item = QTableWidgetItem(action.text().replace("&", "").rstrip("…"))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            editor = QKeySequenceEdit(action.shortcut())
            editor.setClearButtonEnabled(True)
            editor.setMaximumSequenceLength(1)
            self.table.setCellWidget(row, 1, editor)
            self.editors[name] = editor
        self.table.resizeColumnToContents(0)

        self.conflict_label = QLabel()
        self.conflict_label.setStyleSheet("color: #b00020")
        for editor in self.editors.values():
            editor.keySequenceChanged.connect(self._check_conflicts)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self.restore_defaults
        )
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.conflict_label)
        layout.addWidget(buttons)
        self.resize(520, 560)

    def restore_defaults(self) -> None:
        for name, editor in self.editors.items():
            editor.setKeySequence(QKeySequence(self.defaults.get(name, "")))

    def conflicts(self) -> list[str]:
        seen: dict[str, str] = {}
        double: list[str] = []
        for name, editor in self.editors.items():
            text = editor.keySequence().toString()
            if not text:
                continue
            if text in seen:
                double.append(text)
            seen[text] = name
        return double

    def _check_conflicts(self) -> None:
        double = self.conflicts()
        self.conflict_label.setText(
            _tr("Mehrfach vergeben: {keys}").format(keys=", ".join(double)) if double else ""
        )
        self.ok_button.setEnabled(not double)

    def accept(self) -> None:
        settings = QSettings()
        for name, editor in self.editors.items():
            sequence = editor.keySequence()
            self.named[name].setShortcut(sequence)
            settings.setValue(SHORTCUT_KEY.format(name=name), sequence.toString())
        super().accept()
