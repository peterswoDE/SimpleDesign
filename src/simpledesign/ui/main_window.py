"""Main window: menu, toolbar, docks around the 3D viewport."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStyle,
    QWidget,
)

from simpledesign import __version__
from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.io.export import ExportError, export
from simpledesign.ui.cutlist_panel import CutlistPanel
from simpledesign.ui.cutplan_panel import CutplanPanel
from simpledesign.ui.joints_panel import JointsPanel
from simpledesign.ui.materials_dialog import MaterialsDialog
from simpledesign.ui.parameters_panel import ParametersPanel
from simpledesign.ui.properties_panel import PropertiesPanel
from simpledesign.ui.scene import Scene
from simpledesign.ui.sketch_editor import NewPartEditor, new_sketch_part
from simpledesign.ui.templates_dialog import InsertTemplateDialog, insert_template
from simpledesign.ui.tree_panel import TreePanel
from simpledesign.ui.viewport import Viewport


class MainWindow(QMainWindow):
    def __init__(self, controller: Controller | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.resize(1400, 900)

        self.controller = controller or Controller(self)
        self.undo_stack = self.controller.undo_stack

        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)
        self.scene = Scene(self.viewport, self.controller)

        self._create_docks()
        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self.statusBar().showMessage(self.tr("Bereit"), 3000)
        self.scene.status_message.connect(self._show_status)

        self.controller.file_state_changed.connect(self._update_title)
        self._update_title()
        self._restore_layout()

    # ------------------------------------------------------------------ docks
    def _create_docks(self) -> None:
        left = Qt.DockWidgetArea.LeftDockWidgetArea
        right = Qt.DockWidgetArea.RightDockWidgetArea
        bottom = Qt.DockWidgetArea.BottomDockWidgetArea
        self.tree_panel = TreePanel(self.controller)
        self.parts_dock = self._add_dock("parts", self.tr("Bauteile"), self.tree_panel, left)
        self.properties_panel = PropertiesPanel(self.controller)
        self.properties_dock = self._add_dock(
            "properties", self.tr("Eigenschaften"), self.properties_panel, right
        )
        self.parameters_panel = ParametersPanel(self.controller)
        self.parameters_dock = self._add_dock(
            "parameters", self.tr("Parameter"), self.parameters_panel, bottom
        )
        self.cutlist_panel = CutlistPanel(self.controller)
        self.cutlist_dock = self._add_dock(
            "cutlist", self.tr("Zuschnittliste"), self.cutlist_panel, bottom
        )
        self.cutplan_panel = CutplanPanel(self.controller)
        self.cutplan_dock = self._add_dock(
            "cutplan", self.tr("Schnittplan"), self.cutplan_panel, bottom
        )
        self.joints_panel = JointsPanel(self.controller)
        self.joints_dock = self._add_dock(
            "joints", self.tr("Verbindungen"), self.joints_panel, right
        )
        self.joints_panel.joint_created.connect(lambda _id: self.joints_dock.show())
        self.tabifyDockWidget(self.parameters_dock, self.cutlist_dock)
        self.tabifyDockWidget(self.cutlist_dock, self.cutplan_dock)
        self.parameters_dock.raise_()

    def _add_dock(
        self, name: str, title: str, widget: QWidget, area: Qt.DockWidgetArea
    ) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{name}")  # required for saveState()
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    # ------------------------------------------------------------------ actions
    def _create_actions(self) -> None:
        icon = self.style().standardIcon
        pixmap = QStyle.StandardPixmap

        self.action_new = QAction(icon(pixmap.SP_FileIcon), self.tr("&Neu"), self)
        self.action_new.setShortcut(QKeySequence.StandardKey.New)
        self.action_new.triggered.connect(self.new_document)
        self.action_open = QAction(icon(pixmap.SP_DialogOpenButton), self.tr("Ö&ffnen…"), self)
        self.action_open.setShortcut(QKeySequence.StandardKey.Open)
        self.action_open.triggered.connect(self.open_document)
        self.action_save = QAction(icon(pixmap.SP_DialogSaveButton), self.tr("&Speichern"), self)
        self.action_save.setShortcut(QKeySequence.StandardKey.Save)
        self.action_save.triggered.connect(self.save_document)
        self.action_save_as = QAction(self.tr("Speichern &unter…"), self)
        self.action_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.action_save_as.triggered.connect(self.save_document_as)

        self.action_export = QAction(self.tr("3D-Modell &exportieren…"), self)
        self.action_export.setToolTip(self.tr("Als STEP, STL oder OBJ exportieren"))
        self.action_export.triggered.connect(self.export_model)
        self.action_materials = QAction(self.tr("&Materialbibliothek…"), self)
        self.action_materials.triggered.connect(self.edit_materials)

        self.action_quit = QAction(self.tr("&Beenden"), self)
        self.action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quit.triggered.connect(self.close)

        self.action_undo = self.undo_stack.createUndoAction(self, self.tr("&Rückgängig"))
        self.action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.action_undo.setIcon(icon(pixmap.SP_ArrowBack))
        self.action_redo = self.undo_stack.createRedoAction(self, self.tr("&Wiederholen"))
        self.action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.action_redo.setIcon(icon(pixmap.SP_ArrowForward))

        self.action_add_board = QAction(self.tr("Brett"), self)
        self.action_add_board.setToolTip(self.tr("Neues Brett (B)"))
        self.action_add_board.setShortcut(QKeySequence("B"))
        self.action_add_board.triggered.connect(self._add_board)
        self.action_add_group = QAction(self.tr("Gruppe"), self)
        self.action_add_group.setToolTip(self.tr("Neue Gruppe aus der Auswahl (Strg+G)"))
        self.action_add_group.triggered.connect(self.tree_panel.group_selected)
        self.action_add_parameter = QAction(self.tr("Parameter"), self)
        self.action_add_parameter.setToolTip(self.tr("Neuer Parameter"))
        self.action_add_parameter.triggered.connect(self._add_parameter)
        self.action_add_sketch_part = QAction(self.tr("Skizzenteil…"), self)
        self.action_add_sketch_part.setToolTip(
            self.tr("Teil mit frei gezeichneter Kontur (z. B. geschwungene Seitenwand)")
        )
        self.action_add_sketch_part.triggered.connect(self.add_sketch_part)
        self.action_insert_template = QAction(self.tr("Vorlage…"), self)
        self.action_insert_template.setToolTip(
            self.tr("Fertiges Möbel (Regal, Schrank, Tisch, …) einfügen")
        )
        self.action_insert_template.triggered.connect(self.insert_template)

        self.action_snap = QAction(self.tr("An Kanten &einrasten"), self)
        self.action_snap.setCheckable(True)
        self.action_snap.setChecked(True)
        self.action_snap.setToolTip(
            self.tr("Beim Ziehen an Kanten anderer Bretter einrasten (Umschalt: aus)")
        )
        self.action_snap.toggled.connect(self._set_snapping)

        self.view_actions: dict[str, QAction] = {}
        for name, label, shortcut in (
            ("front", self.tr("Vorne"), "Ctrl+1"),
            ("top", self.tr("Oben"), "Ctrl+2"),
            ("right", self.tr("Rechts"), "Ctrl+3"),
            ("iso", self.tr("Isometrisch"), "Ctrl+0"),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, n=name: self.viewport.show_view(n))
            self.view_actions[name] = action

        self.action_fit = QAction(self.tr("Alles &zeigen"), self)
        self.action_fit.setShortcut(QKeySequence("F6"))
        self.action_fit.triggered.connect(self.viewport.fit_view)

        self.action_about = QAction(self.tr("Ü&ber SimpleDesign"), self)
        self.action_about.triggered.connect(self._show_about)

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu(self.tr("&Datei"))
        file_menu.addActions(
            [self.action_new, self.action_open, self.action_save, self.action_save_as]
        )
        file_menu.addSeparator()
        file_menu.addAction(self.action_export)
        file_menu.addAction(self.cutlist_panel.export_action)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        edit_menu = menu_bar.addMenu(self.tr("&Bearbeiten"))
        edit_menu.addActions([self.action_undo, self.action_redo])
        edit_menu.addSeparator()
        tree = self.tree_panel
        edit_menu.addActions(
            [tree.action_rename, tree.action_duplicate, tree.action_delete, tree.action_group]
        )

        insert_menu = menu_bar.addMenu(self.tr("&Einfügen"))
        insert_menu.addActions(
            [
                self.action_add_board,
                self.action_add_sketch_part,
                self.action_add_group,
                self.action_add_parameter,
                self.action_insert_template,
            ]
        )
        insert_menu.addAction(tree.action_save_template)

        joint_menu = menu_bar.addMenu(self.tr("&Verbindung"))
        joint_menu.addActions(list(self.joints_panel.add_actions.values()))

        view_menu = menu_bar.addMenu(self.tr("&Ansicht"))
        view_menu.addActions(list(self.view_actions.values()))
        view_menu.addAction(self.action_fit)
        view_menu.addSeparator()
        view_menu.addActions([tree.action_toggle_visible, tree.action_transparent])
        view_menu.addAction(self.action_snap)
        view_menu.addSeparator()
        for dock in (
            self.parts_dock,
            self.properties_dock,
            self.joints_dock,
            self.parameters_dock,
            self.cutlist_dock,
            self.cutplan_dock,
        ):
            view_menu.addAction(dock.toggleViewAction())

        extras_menu = menu_bar.addMenu(self.tr("E&xtras"))
        extras_menu.addAction(self.action_materials)

        help_menu = menu_bar.addMenu(self.tr("&Hilfe"))
        help_menu.addAction(self.action_about)

    def _create_toolbar(self) -> None:
        toolbar = self.addToolBar(self.tr("Werkzeuge"))
        toolbar.setObjectName("toolbar_main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addActions([self.action_new, self.action_open, self.action_save])
        toolbar.addSeparator()
        toolbar.addActions([self.action_undo, self.action_redo])
        toolbar.addSeparator()
        toolbar.addActions(
            [
                self.action_add_board,
                self.action_add_sketch_part,
                self.action_add_group,
                self.action_add_parameter,
                self.action_insert_template,
            ]
        )
        toolbar.addSeparator()
        toolbar.addActions(list(self.view_actions.values()))

    # ------------------------------------------------------------------ editing
    def _target_group(self) -> str | None:
        """New things go into the selected group (or the group of the selected board)."""
        doc = self.controller.doc
        selection = self.controller.selection
        if len(selection) == 1:
            obj_id = selection[0]
            if obj_id in doc.groups:
                return obj_id
            if obj_id in doc.boards:
                return doc.boards[obj_id].group_id
        return None

    def _add_board(self) -> None:
        first = not self.controller.doc.boards
        self.controller.add_board(self._target_group())
        if first:
            self.viewport.fit_view()

    def _add_parameter(self) -> None:
        self.parameters_dock.show()
        self.parameters_dock.raise_()
        self.parameters_panel.add_parameter()

    def add_sketch_part(self) -> NewPartEditor:
        return new_sketch_part(self, self.controller)

    def insert_template(self) -> None:
        library = self.controller.templates
        dialog = InsertTemplateDialog(library, self)
        if library.errors:
            QMessageBox.warning(
                self,
                self.tr("Vorlagen"),
                self.tr("Einige Vorlagen konnten nicht gelesen werden:\n{0}").format(
                    "\n".join(library.errors)
                ),
            )
        first = not self.controller.doc.boards
        while dialog.exec() == InsertTemplateDialog.DialogCode.Accepted:
            if insert_template(dialog, self.controller.insert_template):
                if first:
                    self.viewport.fit_view()
                return

    def _set_snapping(self, enabled: bool) -> None:
        self.scene.snapping_enabled = enabled

    def _show_status(self, text: str) -> None:
        if text:
            self.statusBar().showMessage(text)
        else:
            self.statusBar().clearMessage()

    # ------------------------------------------------------------------ files
    def _file_filter(self) -> str:
        return self.tr("SimpleDesign-Datei (*{suffix})").format(suffix=sdz.FILE_SUFFIX)

    def _update_title(self) -> None:
        path = self.controller.file_path
        name = path.name if path else self.tr("Unbenannt")
        self.setWindowTitle(f"{name}[*] - SimpleDesign")
        self.setWindowModified(self.controller.modified)

    def maybe_save(self) -> bool:
        """Ask before discarding changes. False = the user cancelled."""
        if not self.controller.modified:
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self,
            "SimpleDesign",
            self.tr("Die Änderungen wurden noch nicht gespeichert. Jetzt speichern?"),
            buttons.Save | buttons.Discard | buttons.Cancel,
        )
        if answer == buttons.Save:
            return self.save_document()
        return answer == buttons.Discard

    def new_document(self) -> None:
        if self.maybe_save():
            self.controller.new_document()
            self.controller.discard_recovery()
            self.viewport.fit_view()

    def open_document(self) -> None:
        if not self.maybe_save():
            return
        start = str(QSettings().value("files/last_dir", ""))
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("Öffnen"), start, self._file_filter()
        )
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> bool:
        try:
            self.controller.open(path)
        except (OSError, sdz.FileFormatError) as error:
            QMessageBox.critical(
                self,
                self.tr("Öffnen"),
                self.tr("Die Datei konnte nicht geöffnet werden:\n{error}").format(error=error),
            )
            return False
        self.controller.discard_recovery()
        QSettings().setValue("files/last_dir", str(path.parent))
        self.viewport.fit_view()
        return True

    def save_document(self) -> bool:
        if self.controller.file_path is None:
            return self.save_document_as()
        return self._save_to(self.controller.file_path)

    def save_document_as(self) -> bool:
        start = self.controller.file_path or Path(
            str(QSettings().value("files/last_dir", "")), self.tr("Unbenannt") + sdz.FILE_SUFFIX
        )
        path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("Speichern unter"), str(start), self._file_filter()
        )
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != sdz.FILE_SUFFIX:
            target = target.with_name(target.name + sdz.FILE_SUFFIX)
        return self._save_to(target)

    def _save_to(self, path: Path) -> bool:
        try:
            self.controller.save(path)
        except OSError as error:
            QMessageBox.critical(
                self,
                self.tr("Speichern"),
                self.tr("Die Datei konnte nicht gespeichert werden:\n{error}").format(error=error),
            )
            return False
        QSettings().setValue("files/last_dir", str(path.parent))
        self.statusBar().showMessage(self.tr("Gespeichert: {path}").format(path=path), 4000)
        return True

    def export_model(self) -> None:
        file_path = self.controller.file_path
        untitled = Path(self.tr("Unbenannt") + ".step")
        start = file_path.with_suffix(".step") if file_path else untitled
        filters = ";;".join(
            (
                self.tr("STEP (*.step *.stp)"),
                self.tr("STL (*.stl)"),
                self.tr("OBJ (*.obj)"),
            )
        )
        path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("3D-Modell exportieren"), str(start), filters
        )
        if path:
            self.export_to(Path(path))

    def export_to(self, path: Path) -> bool:
        try:
            export(self.controller.doc, self.controller.evaluation, path)
        except (OSError, ExportError) as error:
            QMessageBox.critical(
                self,
                self.tr("Export"),
                self.tr("Der Export ist fehlgeschlagen:\n{error}").format(error=error),
            )
            return False
        self.statusBar().showMessage(self.tr("Exportiert: {path}").format(path=path), 4000)
        return True

    # ------------------------------------------------------------------ materials
    def edit_materials(self) -> None:
        dialog = MaterialsDialog(self.controller.library, self)
        if dialog.exec() == MaterialsDialog.DialogCode.Accepted:
            self.apply_library(dialog)

    def apply_library(self, dialog: MaterialsDialog) -> None:
        """Store the edited library; offer to update the document's copies."""
        try:
            self.controller.set_library(dialog.library())
        except OSError as error:
            QMessageBox.critical(
                self,
                self.tr("Materialbibliothek"),
                self.tr("Die Bibliothek konnte nicht gespeichert werden:\n{error}").format(
                    error=error
                ),
            )
            return
        outdated = self.controller.outdated_materials()
        if not outdated:
            return
        names = "\n".join(f"• {self.controller.doc.materials[i].name}" for i in outdated)
        answer = QMessageBox.question(
            self,
            self.tr("Materialbibliothek"),
            self.tr(
                "Diese Materialien des Dokuments wurden in der Bibliothek geändert:\n{names}\n\n"
                "Sollen sie im Dokument aktualisiert werden?"
            ).format(names=names),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.update_materials(outdated)

    def show_library_error(self) -> None:
        """At start: the library file was broken, the built-ins are used."""
        if self.controller.library_error:
            QMessageBox.warning(
                self,
                self.tr("Materialbibliothek"),
                self.tr(
                    "Die Materialbibliothek konnte nicht gelesen werden; es werden die "
                    "Standardmaterialien verwendet.\n{error}"
                ).format(error=self.controller.library_error),
            )

    def offer_recovery(self) -> None:
        """After a crash: offer the autosaved state."""
        if not self.controller.recovery_available():
            return
        answer = QMessageBox.question(
            self,
            self.tr("Wiederherstellen"),
            self.tr(
                "SimpleDesign wurde nicht ordnungsgemäß beendet.\n"
                "Soll der automatisch gesicherte Stand wiederhergestellt werden?"
            ),
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.controller.restore_recovery()
            except (OSError, sdz.FileFormatError) as error:
                QMessageBox.critical(self, self.tr("Wiederherstellen"), str(error))
            self.viewport.fit_view()
        else:
            self.controller.discard_recovery()

    # ------------------------------------------------------------------ misc
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            self.tr("Über SimpleDesign"),
            self.tr("SimpleDesign {version}\n\nEinfaches parametrisches CAD für Holzmöbel.").format(
                version=__version__
            ),
        )

    def _restore_layout(self) -> None:
        settings = QSettings()
        geometry = settings.value("window/geometry")
        state = settings.value("window/state")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.maybe_save():
            event.ignore()
            return
        self.controller.autosave_timer.stop()
        self.controller.discard_recovery()  # clean exit: nothing to recover
        settings = QSettings()
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/state", self.saveState())
        # pyvistaqt needs an explicit close to release the OpenGL render window.
        self.viewport.close()
        super().closeEvent(event)
