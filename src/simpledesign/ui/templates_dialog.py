"""Dialogs for templates: insert a template, save a group as template."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simpledesign.core.params.errors import FormulaError
from simpledesign.core.templates.library import TemplateLibrary
from simpledesign.core.templates.template import Template, TemplateError
from simpledesign.ui.texts import error_message

INDEX_ROLE = Qt.ItemDataRole.UserRole


class InsertTemplateDialog(QDialog):
    """Pick a template and set its parameters before inserting it."""

    def __init__(
        self,
        library: TemplateLibrary,
        parent: QWidget | None = None,
        insert: Callable[[Template, dict[str, str]], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Vorlage einfügen"))
        self._insert = insert
        self.resize(640, 420)
        self.library = library
        self._templates: list[Template] = []
        self._fields: dict[str, QLineEdit] = {}

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_template)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.form = QFormLayout()
        self.remove_button = QPushButton(self.tr("Vorlage löschen"))
        self.remove_button.clicked.connect(self._remove)

        right = QVBoxLayout()
        right.addWidget(self.description)
        right.addLayout(self.form)
        right.addStretch(1)
        right.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignLeft)
        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addLayout(right, 2)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(self.tr("Einfügen"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self._templates = self.library.templates()
        self.list.clear()
        for index, template in enumerate(self._templates):
            label = (
                template.name if template.builtin else self.tr("{0} (eigene)").format(template.name)
            )
            item = QListWidgetItem(label)
            item.setData(INDEX_ROLE, index)
            self.list.addItem(item)
        if self._templates:
            self.list.setCurrentRow(0)

    def _show_template(self, row: int) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._fields = {}
        template = self.template()
        if template is None:
            self.description.clear()
            self.remove_button.setEnabled(False)
            return
        self.remove_button.setEnabled(not template.builtin)
        self.description.setText(template.description)
        try:
            parameters = template.parameters()
        except TemplateError as error:
            self.description.setText(str(error))
            return
        for param in parameters:
            field = QLineEdit(param.expression)
            field.setObjectName(f"param_{param.name}")
            self._fields[param.name] = field
            self.form.addRow(param.name, field)

    def accept(self) -> None:
        """With an ``insert`` callback the dialog only closes once inserting worked."""
        if self._insert is not None and not insert_template(self, self._insert):
            return
        super().accept()

    def template(self) -> Template | None:
        row = self.list.currentRow()
        return self._templates[row] if 0 <= row < len(self._templates) else None

    def parameters(self) -> dict[str, str]:
        """The parameter formulas the user entered (only changed ones)."""
        template = self.template()
        if template is None:
            return {}
        defaults = {p.name: p.expression for p in template.parameters()}
        return {
            name: field.text().strip()
            for name, field in self._fields.items()
            if field.text().strip() != defaults.get(name)
        }

    def _remove(self) -> None:
        template = self.template()
        if template is None or template.builtin:
            return
        answer = QMessageBox.question(
            self,
            self.tr("Vorlage löschen"),
            self.tr("Die Vorlage „{0}“ wirklich löschen?").format(template.name),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.library.remove(template)
            self._reload()


def insert_template(
    dialog: InsertTemplateDialog, insert: Callable[[Template, dict[str, str]], object]
) -> bool:
    """Run ``insert(template, parameters)``; show formula errors. True on success."""
    template = dialog.template()
    if template is None:
        return False
    try:
        insert(template, dialog.parameters())
    except (FormulaError, KeyError, TemplateError) as error:
        text = error_message(error) if isinstance(error, FormulaError) else str(error)
        QMessageBox.warning(dialog, dialog.tr("Vorlage einfügen"), text)
        return False
    return True


class SaveTemplateDialog(QDialog):
    """Name and description for "Gruppe als Vorlage speichern"."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Gruppe als Vorlage speichern"))
        self.name = QLineEdit(name)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(self.tr("Kurze Beschreibung (optional)"))
        hint = QLabel(
            self.tr(
                "Die Parameter der Gruppe werden zu den Parametern der Vorlage. "
                "Bezüge auf Teile oder Parameter außerhalb der Gruppe werden durch "
                "ihren aktuellen Wert ersetzt."
            )
        )
        hint.setWordWrap(True)
        form = QFormLayout()
        form.addRow(self.tr("Name"), self.name)
        form.addRow(self.tr("Beschreibung"), self.description)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if self.name.text().strip():
            self.accept()
