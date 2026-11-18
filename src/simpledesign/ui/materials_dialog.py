"""Editor for the material library (Extras > Materialbibliothek).

The dialog works on a copy; :meth:`MaterialsDialog.library` returns the edited
library after the user pressed OK. Optional numbers (fixed width, stock size,
price) use 0 for "not set", shown as "–".
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simpledesign.core.ids import new_id
from simpledesign.core.materials.library import MaterialLibrary
from simpledesign.core.materials.material import Material, MaterialKind
from simpledesign.ui.texts import material_kind_label

ID_ROLE = Qt.ItemDataRole.UserRole


def _spin(maximum: float, suffix: str, optional: bool = False, decimals: int = 1) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, maximum)
    spin.setDecimals(decimals)
    spin.setSuffix(suffix)
    if optional:
        spin.setSpecialValueText("–")  # shown for the minimum (0) = not set
    return spin


def _optional(value: float) -> float | None:
    return value if value > 0 else None


class MaterialsDialog(QDialog):
    def __init__(self, library: MaterialLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Materialbibliothek"))
        self.resize(760, 520)
        self.materials: dict[str, Material] = dict(library.materials)
        self._updating = False

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._show_current)
        self.new_button = QPushButton(self.tr("Neu"))
        self.new_button.clicked.connect(self.add_material)
        self.duplicate_button = QPushButton(self.tr("Duplizieren"))
        self.duplicate_button.clicked.connect(self.duplicate_material)
        self.delete_button = QPushButton(self.tr("Löschen"))
        self.delete_button.clicked.connect(self.delete_material)
        buttons_row = QHBoxLayout()
        for button in (self.new_button, self.duplicate_button, self.delete_button):
            buttons_row.addWidget(button)
        left = QVBoxLayout()
        left.addWidget(self.list)
        left.addLayout(buttons_row)

        self.name_edit = QLineEdit()
        self.kind_combo = QComboBox()
        for kind in MaterialKind:
            self.kind_combo.addItem(material_kind_label(kind), kind.value)
        self.thickness_spin = _spin(1000, " mm")
        self.thickness_spin.setMinimum(0.1)
        self.width_spin = _spin(1000, " mm", optional=True)
        self.width_spin.setToolTip(self.tr("Feste Breite bei Kantholz/Latten"))
        self.grain_check = QCheckBox(self.tr("Länge muss in Faserrichtung liegen"))
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        self.stock_length_spin = _spin(20000, " mm", optional=True, decimals=0)
        self.stock_width_spin = _spin(5000, " mm", optional=True, decimals=0)
        self.edge_trim_spin = _spin(100, " mm")
        self.price_spin = _spin(100000, " €", optional=True, decimals=2)

        form = QFormLayout()
        form.addRow(self.tr("Name"), self.name_edit)
        form.addRow(self.tr("Art"), self.kind_combo)
        form.addRow(self.tr("Dicke"), self.thickness_spin)
        form.addRow(self.tr("Breite (fest)"), self.width_spin)
        form.addRow(self.tr("Faser"), self.grain_check)
        form.addRow(self.tr("Farbe"), self.color_button)
        form.addRow(self.tr("Rohmaß Länge"), self.stock_length_spin)
        form.addRow(self.tr("Rohmaß Breite"), self.stock_width_spin)
        form.addRow(self.tr("Besäumrand"), self.edge_trim_spin)
        form.addRow(self.tr("Preis pro Stück"), self.price_spin)
        self.form_widget = QWidget()
        self.form_widget.setLayout(form)

        self.name_edit.textEdited.connect(self._store)
        self.kind_combo.activated.connect(self._store)
        self.grain_check.toggled.connect(self._store)
        for spin in self._spins():
            spin.valueChanged.connect(self._store)

        top = QHBoxLayout()
        top.addLayout(left, 1)
        top.addWidget(self.form_widget, 2)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(box)

        self._fill_list()
        self.list.setCurrentRow(0)

    def _spins(self) -> tuple[QDoubleSpinBox, ...]:
        return (
            self.thickness_spin,
            self.width_spin,
            self.stock_length_spin,
            self.stock_width_spin,
            self.edge_trim_spin,
            self.price_spin,
        )

    # ------------------------------------------------------------------ result
    def library(self) -> MaterialLibrary:
        return MaterialLibrary(self.materials.values())

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(ID_ROLE)) if item else None

    # ------------------------------------------------------------------ list
    def _fill_list(self, select: str | None = None) -> None:
        self.list.clear()
        for material in self.materials.values():
            item = QListWidgetItem(material.name)
            item.setData(ID_ROLE, material.id)
            self.list.addItem(item)
            if material.id == select:
                self.list.setCurrentItem(item)
        self.delete_button.setEnabled(len(self.materials) > 1)

    def _insert(self, material: Material) -> None:
        self.materials[material.id] = material
        self._fill_list(select=material.id)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def add_material(self) -> None:
        self._insert(
            Material(
                new_id(),
                self.tr("Neues Material"),
                MaterialKind.PANEL,
                18,
                stock_length=2500,
                stock_width=1250,
                edge_trim=10,
            )
        )

    def duplicate_material(self) -> None:
        if (material_id := self.current_id()) is None:
            return
        original = self.materials[material_id]
        name = self.tr("{name} (Kopie)").format(name=original.name)
        self._insert(dataclasses.replace(original, id=new_id(), name=name))

    def delete_material(self) -> None:
        material_id = self.current_id()
        if material_id is None or len(self.materials) <= 1:
            return
        row = self.list.currentRow()
        del self.materials[material_id]
        self._fill_list()
        self.list.setCurrentRow(min(row, self.list.count() - 1))

    # ------------------------------------------------------------------ form
    def _show_current(self) -> None:
        material_id = self.current_id()
        self.form_widget.setEnabled(material_id is not None)
        if material_id is None:
            return
        m = self.materials[material_id]
        self._updating = True
        try:
            self.name_edit.setText(m.name)
            self.kind_combo.setCurrentIndex(self.kind_combo.findData(m.kind.value))
            self.thickness_spin.setValue(m.thickness)
            self.width_spin.setValue(m.width or 0)
            self.grain_check.setChecked(m.has_grain)
            self._show_color(m.color)
            self.stock_length_spin.setValue(m.stock_length or 0)
            self.stock_width_spin.setValue(m.stock_width or 0)
            self.edge_trim_spin.setValue(m.edge_trim)
            self.price_spin.setValue(m.price or 0)
            self._update_enabled()
        finally:
            self._updating = False

    def _update_enabled(self) -> None:
        linear = MaterialKind(self.kind_combo.currentData()) == MaterialKind.TIMBER
        self.width_spin.setEnabled(linear)
        self.stock_width_spin.setEnabled(not linear)
        self.edge_trim_spin.setEnabled(not linear)

    def _show_color(self, color: str) -> None:
        self.color_button.setText(color)
        self.color_button.setStyleSheet(f"QPushButton {{ background: {color}; }}")

    def _store(self) -> None:
        """Copy the form into the edited material."""
        material_id = self.current_id()
        if self._updating or material_id is None:
            return
        self._update_enabled()
        kind = MaterialKind(self.kind_combo.currentData())
        linear = kind == MaterialKind.TIMBER
        material = dataclasses.replace(
            self.materials[material_id],
            name=self.name_edit.text().strip() or self.materials[material_id].name,
            kind=kind,
            thickness=self.thickness_spin.value(),
            width=_optional(self.width_spin.value()) if linear else None,
            has_grain=self.grain_check.isChecked(),
            color=self.color_button.text(),
            stock_length=_optional(self.stock_length_spin.value()),
            stock_width=None if linear else _optional(self.stock_width_spin.value()),
            edge_trim=0.0 if linear else self.edge_trim_spin.value(),
            price=_optional(self.price_spin.value()),
        )
        self.materials[material_id] = material
        item = self.list.currentItem()
        if item is not None:
            item.setText(material.name)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.color_button.text()), self)
        if color.isValid():
            self.set_color(color.name())

    def set_color(self, color: str) -> None:
        self._show_color(color)
        self._store()
