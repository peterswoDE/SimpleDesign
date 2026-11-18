"""Parameter table: Name | Formel | Wert, grouped by scope (global, groups)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.params.errors import FormulaError
from simpledesign.ui.texts import error_message, number

ID_ROLE = Qt.ItemDataRole.UserRole
SCOPE_ROLE = Qt.ItemDataRole.UserRole + 1
GLOBAL_SCOPE = ""  # item data for "no group"
COL_NAME, COL_FORMULA, COL_VALUE = 0, 1, 2
ERROR_BRUSH = QBrush(QColor("#fde2e2"))


class ParametersPanel(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._updating = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([self.tr("Name"), self.tr("Formel"), self.tr("Wert")])
        self.tree.setColumnWidth(COL_NAME, 160)
        self.tree.setColumnWidth(COL_FORMULA, 260)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        self.add_button = QPushButton(self.tr("Parameter hinzufügen"))
        self.add_button.clicked.connect(self.add_parameter)
        self.delete_button = QPushButton(self.tr("Löschen"))
        self.delete_button.clicked.connect(self.delete_selected)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        layout.addLayout(buttons)

        controller.document_changed.connect(self.rebuild)
        self.rebuild()

    # ------------------------------------------------------------------ building
    def _group_path(self, group_id: str) -> str:
        doc = self.controller.doc
        chain = [g for g in doc.scope_chain(group_id) if g is not None]
        return " / ".join(doc.groups[g].name for g in reversed(chain))

    def rebuild(self) -> None:
        doc = self.controller.doc
        evaluation = self.controller.evaluation
        self._updating = True
        self.tree.clear()

        scopes: list[tuple[str | None, str]] = [(None, self.tr("Global"))]
        scopes += sorted(((g, self._group_path(g)) for g in doc.groups), key=lambda s: s[1])
        for scope, title in scopes:
            params = sorted(
                (p for p in doc.parameters.values() if p.group_id == scope), key=lambda p: p.name
            )
            if scope is not None and not params:
                continue
            header = QTreeWidgetItem([title])
            header.setData(0, SCOPE_ROLE, scope or GLOBAL_SCOPE)
            font = header.font(0)
            font.setBold(True)
            header.setFont(0, font)
            header.setFirstColumnSpanned(False)
            self.tree.addTopLevelItem(header)
            for param in params:
                item = QTreeWidgetItem(
                    [
                        param.name,
                        doc.display_expression(param.id),
                        number(evaluation.value(param.id)),
                    ]
                )
                item.setData(0, ID_ROLE, param.id)
                item.setData(0, SCOPE_ROLE, scope or GLOBAL_SCOPE)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(COL_VALUE, Qt.AlignmentFlag.AlignRight)
                if error := evaluation.error(param.id):
                    self._mark_error(item, COL_FORMULA, error_message(error))
                header.addChild(item)
            header.setExpanded(True)
        self._updating = False

    def _mark_error(self, item: QTreeWidgetItem, column: int, message: str) -> None:
        item.setBackground(column, ERROR_BRUSH)
        item.setToolTip(column, message)

    # ------------------------------------------------------------------ editing
    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        if item.data(0, ID_ROLE) is not None and column in (COL_NAME, COL_FORMULA):
            self.tree.editItem(item, column)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        param_id = item.data(0, ID_ROLE)
        if self._updating or param_id is None:
            return
        text = item.text(column).strip()
        doc = self.controller.doc
        try:
            if column == COL_NAME and text != doc.name_of(param_id):
                self.controller.rename(param_id, text)
            elif column == COL_FORMULA and text != doc.display_expression(param_id):
                self.controller.set_expression(param_id, None, text)
        except FormulaError as error:
            self._updating = True
            self._mark_error(item, column, error_message(error))
            self._updating = False

    def _current_scope(self) -> str | None:
        item = self.tree.currentItem()
        if item is not None:
            scope = item.data(0, SCOPE_ROLE)
            if scope:
                return str(scope)
        selection = self.controller.selection
        doc = self.controller.doc
        if len(selection) == 1:
            obj_id = selection[0]
            if obj_id in doc.groups:
                return obj_id
            if obj_id in doc.boards:
                return doc.boards[obj_id].group_id
        return None

    def add_parameter(self) -> None:
        param_id = self.controller.add_parameter(self._current_scope())
        for item in self._all_items():
            if item.data(0, ID_ROLE) == param_id:
                self.tree.setCurrentItem(item)
                self.tree.editItem(item, COL_NAME)

    def _all_items(self) -> list[QTreeWidgetItem]:
        result: list[QTreeWidgetItem] = []
        for i in range(self.tree.topLevelItemCount()):
            header = self.tree.topLevelItem(i)
            if header is None:
                continue
            for j in range(header.childCount()):
                if (child := header.child(j)) is not None:
                    result.append(child)
        return result

    def delete_selected(self) -> None:
        ids = [
            str(item.data(0, ID_ROLE))
            for item in self.tree.selectedItems()
            if item.data(0, ID_ROLE) is not None
        ]
        if not ids:
            return
        doc = self.controller.doc
        refs = doc.external_references(ids)
        if refs:
            users = sorted({doc.name_of(owner) for owner, _prop in refs})
            answer = QMessageBox.question(
                self,
                self.tr("Parameter löschen"),
                self.tr(
                    "Der Parameter wird noch verwendet von:\n{users}\n\n"
                    "Die Formeln erhalten dann feste Werte. Trotzdem löschen?"
                ).format(users=", ".join(users)),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.controller.delete(ids)
