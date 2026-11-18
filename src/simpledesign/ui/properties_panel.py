"""Properties of the selected board or group."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.model.part import DIMENSIONS, PATTERN, POSITION, Axis, Orientation
from simpledesign.core.params.errors import FormulaError
from simpledesign.ui.expression_field import ExpressionField
from simpledesign.ui.texts import axis_label, error_message, orientation_label, property_label


class PropertiesPanel(QScrollArea):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._updating = False
        self.setWidgetResizable(True)

        self.stack = QStackedWidget()
        self.setWidget(self.stack)

        self.empty_label = QLabel(self.tr("Kein Teil ausgewählt"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setEnabled(False)
        self.stack.addWidget(self.empty_label)

        self._build_board_page()
        self._build_group_page()

        controller.document_changed.connect(self.refresh)
        controller.selection_changed.connect(self.refresh)
        controller.library_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ pages
    def _build_board_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        general = QGroupBox(self.tr("Allgemein"))
        form = QFormLayout(general)
        self.board_name = QLineEdit()
        self.board_name.editingFinished.connect(self._on_board_name)
        form.addRow(self.tr("Name"), self.board_name)
        self.material_combo = QComboBox()
        self.material_combo.activated.connect(self._on_material)
        form.addRow(self.tr("Material"), self.material_combo)
        # Shown when the library version of the material was changed later.
        self.material_hint = QWidget()
        hint_layout = QVBoxLayout(self.material_hint)
        hint_layout.setContentsMargins(0, 0, 0, 0)
        hint_label = QLabel(self.tr("Die Bibliothek enthält eine geänderte Fassung."))
        hint_label.setWordWrap(True)
        self.material_update_button = QPushButton(self.tr("Aus Bibliothek aktualisieren"))
        self.material_update_button.clicked.connect(self._on_update_material)
        hint_layout.addWidget(hint_label)
        hint_layout.addWidget(self.material_update_button)
        form.addRow("", self.material_hint)
        self.ignore_grain_check = QCheckBox(self.tr("Faserrichtung ignorieren"))
        self.ignore_grain_check.setToolTip(
            self.tr("Das Teil darf im Zuschnitt gedreht werden (z. B. bei verdeckten Teilen).")
        )
        self.ignore_grain_check.clicked.connect(self._on_ignore_grain)
        form.addRow("", self.ignore_grain_check)
        self.orientation_combo = QComboBox()
        for orientation in Orientation:
            self.orientation_combo.addItem(orientation_label(orientation), orientation.value)
        self.orientation_combo.activated.connect(self._on_orientation)
        form.addRow(self.tr("Lage"), self.orientation_combo)
        layout.addWidget(general)

        self.fields: dict[str, ExpressionField] = {}
        for title, props in (
            (self.tr("Maße"), DIMENSIONS),
            (self.tr("Position"), POSITION),
            (self.tr("Anordnung"), PATTERN),
        ):
            box = QGroupBox(title)
            box_form = QFormLayout(box)
            for prop in props:
                field = ExpressionField()
                field.submitted.connect(lambda text, p=prop: self._on_expression(p, text))
                self.fields[prop] = field
                box_form.addRow(property_label(prop), field)
            if props is PATTERN:
                self.pattern_axis_combo = QComboBox()
                for axis in Axis:
                    self.pattern_axis_combo.addItem(axis_label(axis), axis.value)
                self.pattern_axis_combo.activated.connect(self._on_pattern_axis)
                box_form.addRow(self.tr("Richtung"), self.pattern_axis_combo)
            layout.addWidget(box)
        layout.addStretch(1)
        self.board_page = page
        self.stack.addWidget(page)

    def _build_group_page(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.group_name = QLineEdit()
        self.group_name.editingFinished.connect(self._on_group_name)
        form.addRow(self.tr("Gruppe"), self.group_name)
        self.group_page = page
        self.stack.addWidget(page)

    # ------------------------------------------------------------------ refresh
    def _single(self) -> str | None:
        selection = self.controller.selection
        return selection[0] if len(selection) == 1 else None

    def refresh(self) -> None:
        doc = self.controller.doc
        evaluation = self.controller.evaluation
        selected = self._single()
        self._updating = True
        try:
            if selected in doc.boards:
                board = doc.boards[selected]
                self.stack.setCurrentWidget(self.board_page)
                self.board_name.setText(board.name)
                self.board_name.setStyleSheet("")
                self._fill_materials()
                self.material_combo.setCurrentIndex(self.material_combo.findData(board.material_id))
                self.material_hint.setVisible(
                    board.material_id in self.controller.outdated_materials()
                )
                material = doc.materials[board.material_id]
                self.ignore_grain_check.setEnabled(material.has_grain)
                self.ignore_grain_check.setChecked(board.ignore_grain or not material.has_grain)
                index = self.orientation_combo.findData(board.orientation.value)
                self.orientation_combo.setCurrentIndex(index)
                axis_index = self.pattern_axis_combo.findData(board.pattern_axis.value)
                self.pattern_axis_combo.setCurrentIndex(axis_index)
                for prop, field in self.fields.items():
                    error = evaluation.error(board.id, prop)
                    field.set_state(
                        doc.display_expression(board.id, prop),
                        evaluation.value(board.id, prop),
                        error_message(error) if error else None,
                    )
            elif selected in doc.groups:
                self.stack.setCurrentWidget(self.group_page)
                self.group_name.setText(doc.groups[selected].name)
                self.group_name.setStyleSheet("")
            else:
                count = len(self.controller.selection)
                self.empty_label.setText(
                    self.tr("{n} Teile ausgewählt").format(n=count)
                    if count
                    else self.tr("Kein Teil ausgewählt")
                )
                self.stack.setCurrentWidget(self.empty_label)
        finally:
            self._updating = False

    def _fill_materials(self) -> None:
        """Library materials, then materials only this document has."""
        combo = self.material_combo
        combo.clear()
        for material in self.controller.library:
            combo.addItem(material.name, material.id)
        for material in self.controller.doc.materials.values():
            if material.id not in self.controller.library:
                combo.addItem(
                    self.tr("{name} (nur im Dokument)").format(name=material.name), material.id
                )

    # ------------------------------------------------------------------ edits
    def _on_expression(self, prop: str, text: str) -> None:
        board_id = self._single()
        if board_id is None:
            return
        try:
            self.controller.set_expression(board_id, prop, text)
        except FormulaError as error:
            self.fields[prop].show_input_error(error_message(error))

    def _rename(self, edit: QLineEdit) -> None:
        obj_id = self._single()
        if self._updating or obj_id is None:
            return
        name = edit.text().strip()
        if name == self.controller.doc.name_of(obj_id):
            return
        try:
            self.controller.rename(obj_id, name)
        except FormulaError as error:
            edit.setStyleSheet("QLineEdit { background: #fde2e2; }")
            edit.setToolTip(error_message(error))

    def _on_board_name(self) -> None:
        self._rename(self.board_name)

    def _on_group_name(self) -> None:
        self._rename(self.group_name)

    def _on_material(self, index: int) -> None:
        if (board_id := self._single()) is not None:
            self.controller.set_material([board_id], str(self.material_combo.itemData(index)))

    def _on_update_material(self) -> None:
        if (board_id := self._single()) is not None:
            self.controller.update_materials([self.controller.doc.boards[board_id].material_id])

    def _on_ignore_grain(self, checked: bool) -> None:
        if (board_id := self._single()) is not None:
            self.controller.set_ignore_grain([board_id], checked)

    def _on_orientation(self, index: int) -> None:
        if (board_id := self._single()) is not None:
            value = str(self.orientation_combo.itemData(index))
            self.controller.set_orientation(board_id, Orientation(value))

    def _on_pattern_axis(self, index: int) -> None:
        if (board_id := self._single()) is not None:
            value = str(self.pattern_axis_combo.itemData(index))
            self.controller.set_pattern_axis(board_id, Axis(value))
