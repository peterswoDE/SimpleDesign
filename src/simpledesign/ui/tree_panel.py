"""Parts tree: groups and boards, selection, context menu."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.params.errors import FormulaError

if TYPE_CHECKING:
    from simpledesign.ui.templates_dialog import SaveTemplateDialog
from simpledesign.ui.texts import error_message

ID_ROLE = Qt.ItemDataRole.UserRole
HIDDEN_COLOR = QColor("#9aa5b1")


class TreePanel(QWidget):
    # A group was saved as template (the furniture library reloads).
    template_saved = Signal()

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._updating = False
        self._items: dict[str, QTreeWidgetItem] = {}

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)

        self._create_actions()
        controller.document_changed.connect(self.rebuild)
        controller.selection_changed.connect(self._sync_selection)
        self.rebuild()

    # ------------------------------------------------------------------ actions
    def _create_actions(self) -> None:
        def action(
            text: str, shortcut: str | QKeySequence.StandardKey | None, slot: object
        ) -> QAction:
            a = QAction(text, self)
            if shortcut is not None:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        self.action_rename = action(self.tr("Umbenennen"), "F2", self.rename_current)
        self.action_duplicate = action(self.tr("Duplizieren"), "Ctrl+D", self.duplicate_selected)
        self.action_delete = action(
            self.tr("Löschen"), QKeySequence.StandardKey.Delete, self.delete_selected
        )
        self.action_toggle_visible = action(self.tr("Ein-/Ausblenden"), "H", self.toggle_visible)
        self.action_transparent = action(self.tr("Transparent"), "T", self.toggle_transparent)
        self.action_group = action(
            self.tr("Neue Gruppe aus Auswahl"), "Ctrl+G", self.group_selected
        )
        self.action_save_template = action(
            self.tr("Gruppe als Vorlage speichern…"), None, self.save_group_as_template
        )

    def selected_group(self) -> str | None:
        """The id of the one selected group, if exactly one group is selected."""
        ids = self.selected_ids()
        return ids[0] if len(ids) == 1 and ids[0] in self.controller.doc.groups else None

    def save_group_as_template(self) -> QDialog | None:
        """Ask for name and description (non-blocking), then save."""
        group = self.selected_group()
        if group is None:
            return None
        from simpledesign.ui.templates_dialog import SaveTemplateDialog

        dialog = SaveTemplateDialog(self.controller.doc.groups[group].name, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(partial(self._save_template, group, dialog))
        dialog.open()
        return dialog

    def _save_template(self, group: str, dialog: SaveTemplateDialog) -> None:
        name = dialog.name.text().strip()
        if self.controller.templates.path_for(name).exists():
            answer = QMessageBox.question(
                self,
                self.tr("Vorlage speichern"),
                self.tr("Die Vorlage „{0}“ gibt es schon. Ersetzen?").format(name),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.controller.save_group_as_template(
                group, name, dialog.description.toPlainText().strip()
            )
        except OSError as error:
            QMessageBox.warning(self, self.tr("Vorlage speichern"), str(error))
            return
        self.template_saved.emit()

    # ------------------------------------------------------------------ building
    def rebuild(self) -> None:
        doc = self.controller.doc
        expanded = {i for i, item in self._items.items() if item.isExpanded()}
        known = set(self._items)
        self._updating = True
        self.tree.clear()
        self._items = {}

        def make(obj_id: str, name: str, visible: bool, is_group: bool) -> QTreeWidgetItem:
            item = QTreeWidgetItem([name])
            item.setData(0, ID_ROLE, obj_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            if not visible:
                item.setForeground(0, QBrush(HIDDEN_COLOR))
            if is_group:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            self._items[obj_id] = item
            return item

        def fill(parent_id: str | None, parent: QTreeWidgetItem | None) -> None:
            groups = sorted(
                (g for g in doc.groups.values() if g.parent_id == parent_id), key=lambda g: g.name
            )
            boards = sorted(
                (b for b in doc.boards.values() if b.group_id == parent_id), key=lambda b: b.name
            )
            for group in groups:
                item = make(group.id, group.name, group.visible, True)
                self._attach(item, parent)
                fill(group.id, item)
                item.setExpanded(group.id in expanded or group.id not in known)
            for board in boards:
                self._attach(make(board.id, board.name, doc.is_visible(board), False), parent)

        fill(None, None)
        self._updating = False
        self._sync_selection()

    def _attach(self, item: QTreeWidgetItem, parent: QTreeWidgetItem | None) -> None:
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)

    # ------------------------------------------------------------------ selection
    def selected_ids(self) -> list[str]:
        """The controller's selection (tree, 3D view and panels share it)."""
        return list(self.controller.selection)

    def _tree_selected_ids(self) -> list[str]:
        return [str(item.data(0, ID_ROLE)) for item in self.tree.selectedItems()]

    def _on_tree_selection(self) -> None:
        if not self._updating:
            self.controller.set_selection(self._tree_selected_ids())

    def _sync_selection(self) -> None:
        self._updating = True
        self.tree.clearSelection()
        current = None
        for obj_id in self.controller.selection:
            item = self._items.get(obj_id)
            if item is not None:
                item.setSelected(True)
                current = item
        if current is not None:
            self.tree.scrollToItem(current)
        self._updating = False

    # ------------------------------------------------------------------ editing
    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating:
            return
        obj_id = str(item.data(0, ID_ROLE))
        name = item.text(0).strip()
        if name == self.controller.doc.name_of(obj_id):
            return
        try:
            self.controller.rename(obj_id, name)
        except FormulaError as error:
            QMessageBox.warning(self, self.tr("Umbenennen"), error_message(error))
            self.rebuild()

    def rename_current(self) -> None:
        selection = self.controller.selection
        item = self._items.get(selection[0]) if selection else self.tree.currentItem()
        if item is not None:
            self.tree.setFocus()
            self.tree.setCurrentItem(item)
            self.tree.editItem(item)

    def duplicate_selected(self) -> None:
        if ids := self.selected_ids():
            self.controller.duplicate(ids)

    def delete_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        doc = self.controller.doc
        refs = doc.external_references(ids)
        if refs:
            users = sorted({doc.name_of(owner) for owner, _prop in refs})
            answer = QMessageBox.question(
                self,
                self.tr("Löschen"),
                self.tr(
                    "Diese Teile werden noch in Formeln verwendet von:\n{users}\n\n"
                    "Die Formeln erhalten dann feste Werte. Trotzdem löschen?"
                ).format(users=", ".join(users)),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.controller.delete(ids)

    def toggle_visible(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        doc = self.controller.doc
        first = doc.boards.get(ids[0]) or doc.groups[ids[0]]
        self.controller.set_visible(ids, not first.visible)

    def toggle_transparent(self) -> None:
        boards = [i for i in self.selected_ids() if i in self.controller.doc.boards]
        if boards:
            first = self.controller.doc.boards[boards[0]]
            self.controller.set_transparent(boards, not first.transparent)

    def group_selected(self) -> None:
        ids = self.selected_ids()
        doc = self.controller.doc
        parent = None
        if ids:
            first = ids[0]
            parent = (
                doc.boards[first].group_id if first in doc.boards else doc.groups[first].parent_id
            )
        self.controller.add_group(parent, ids)

    def _move_to(self, group_id: str | None) -> None:
        try:
            self.controller.move_to_group(self.selected_ids(), group_id)
        except ValueError:
            QMessageBox.warning(
                self,
                self.tr("Verschieben"),
                self.tr("Eine Gruppe kann nicht in sich selbst verschoben werden."),
            )

    # ------------------------------------------------------------------ menu
    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        has_selection = bool(self.selected_ids())
        if has_selection:
            menu.addActions([self.action_rename, self.action_duplicate, self.action_delete])
            menu.addSeparator()
            menu.addActions([self.action_toggle_visible, self.action_transparent])
            move_menu = menu.addMenu(self.tr("In Gruppe verschieben"))
            move_menu.addAction(self.tr("(oberste Ebene)"), lambda: self._move_to(None))
            groups = sorted(self.controller.doc.groups.values(), key=lambda g: g.name)
            for group in groups:
                move_menu.addAction(group.name, partial(self._move_to, group.id))
            menu.addSeparator()
        menu.addAction(self.action_group)
        if self.selected_group() is not None:
            menu.addAction(self.action_save_template)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(self.tree.viewport().mapToGlobal(pos))
