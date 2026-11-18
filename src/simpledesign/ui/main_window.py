"""Main window: menu, toolbar, docks around the 3D viewport."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStyle,
    QTreeWidget,
    QWidget,
)

from simpledesign import __version__
from simpledesign.ui.viewport import Viewport


def _placeholder(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setEnabled(False)  # greyed out
    label.setMargin(12)
    return label


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SimpleDesign")
        self.resize(1400, 900)

        self.undo_stack = QUndoStack(self)

        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        self._create_docks()
        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self.statusBar().showMessage(self.tr("Bereit"))

        self._restore_layout()

    # ------------------------------------------------------------------ docks
    def _create_docks(self) -> None:
        self.parts_tree = QTreeWidget()
        self.parts_tree.setHeaderHidden(True)
        self.parts_dock = self._add_dock(
            "parts", self.tr("Bauteile"), self.parts_tree, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.properties_dock = self._add_dock(
            "properties",
            self.tr("Eigenschaften"),
            _placeholder(self.tr("Kein Teil ausgewählt")),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.parameters_dock = self._add_dock(
            "parameters",
            self.tr("Parameter"),
            _placeholder(self.tr("Noch keine Parameter")),
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )

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
        self.action_open = QAction(icon(pixmap.SP_DialogOpenButton), self.tr("Ö&ffnen…"), self)
        self.action_open.setShortcut(QKeySequence.StandardKey.Open)
        self.action_save = QAction(icon(pixmap.SP_DialogSaveButton), self.tr("&Speichern"), self)
        self.action_save.setShortcut(QKeySequence.StandardKey.Save)
        # File handling arrives in phase 1.
        for action in (self.action_new, self.action_open, self.action_save):
            action.setEnabled(False)

        self.action_quit = QAction(self.tr("&Beenden"), self)
        self.action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quit.triggered.connect(self.close)

        self.action_undo = self.undo_stack.createUndoAction(self, self.tr("&Rückgängig"))
        self.action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.action_undo.setIcon(icon(pixmap.SP_ArrowBack))
        self.action_redo = self.undo_stack.createRedoAction(self, self.tr("&Wiederholen"))
        self.action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.action_redo.setIcon(icon(pixmap.SP_ArrowForward))

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
        file_menu.addActions([self.action_new, self.action_open, self.action_save])
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        edit_menu = menu_bar.addMenu(self.tr("&Bearbeiten"))
        edit_menu.addActions([self.action_undo, self.action_redo])

        view_menu = menu_bar.addMenu(self.tr("&Ansicht"))
        view_menu.addActions(list(self.view_actions.values()))
        view_menu.addAction(self.action_fit)
        view_menu.addSeparator()
        for dock in (self.parts_dock, self.properties_dock, self.parameters_dock):
            view_menu.addAction(dock.toggleViewAction())

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
        toolbar.addActions(list(self.view_actions.values()))

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
        settings = QSettings()
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/state", self.saveState())
        # pyvistaqt needs an explicit close to release the OpenGL render window.
        self.viewport.close()
        super().closeEvent(event)
