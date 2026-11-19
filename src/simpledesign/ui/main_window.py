"""Main window: menu, toolbar, docks around the 3D viewport."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStyle,
    QToolBar,
    QToolButton,
    QWidget,
)

from simpledesign import __version__
from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.io.export import ExportError, export
from simpledesign.core.model import arrange, fit, front
from simpledesign.core.model.arrange import AlignMode
from simpledesign.core.model.part import Axis
from simpledesign.core.params.expression import format_number
from simpledesign.core.templates.template import Template
from simpledesign.ui.array_dialog import ArrayDialog
from simpledesign.ui.cutlist_panel import CutlistPanel
from simpledesign.ui.cutplan_panel import CutplanPanel
from simpledesign.ui.drawing_dialog import DrawingDialog
from simpledesign.ui.groove_dialog import GrooveDialog
from simpledesign.ui.joints_panel import JointsPanel
from simpledesign.ui.labels_dialog import LabelsDialog
from simpledesign.ui.library_panel import LibraryPanel
from simpledesign.ui.materials_dialog import MaterialsDialog
from simpledesign.ui.parameters_panel import ParametersPanel
from simpledesign.ui.properties_panel import PropertiesPanel
from simpledesign.ui.scene import Scene
from simpledesign.ui.settings_dialog import (
    Preferences,
    SettingsDialog,
    ShortcutsDialog,
    apply_saved_shortcuts,
)
from simpledesign.ui.shortcut_labels import show_shortcut
from simpledesign.ui.sketch_editor import NewPartEditor, new_sketch_part
from simpledesign.ui.templates_dialog import InsertTemplateDialog
from simpledesign.ui.tools import (
    FaceMeasureTool,
    FrontTool,
    InsertTool,
    MeasureTool,
    SetDistanceTool,
    ShelfPinTool,
    Tool,
)
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
        self._init_shortcuts()
        self.apply_preferences(Preferences.load())
        self.statusBar().showMessage(self.tr("Bereit"), 3000)
        self.scene.status_message.connect(self._show_status)
        self.scene.context_menu_requested.connect(self.show_view_menu)
        self.viewport.template_dropped.connect(self.drop_template)
        self.navigation_hint = QLabel(
            self.tr(
                "Links ziehen: drehen · Rechts/Mitte ziehen: schieben · Rad: zoomen · "
                "Teil ziehen: verschieben · Rechtsklick: Menü"
            )
        )
        self.navigation_hint.setStyleSheet("color: palette(placeholder-text)")
        self.selection_info = QLabel()
        self.statusBar().addPermanentWidget(self.selection_info)
        self.statusBar().addPermanentWidget(self.navigation_hint)
        self.controller.selection_changed.connect(self._update_selection_info)
        self.controller.document_changed.connect(self._update_selection_info)
        self._update_selection_info()

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
            "joints", self.tr("Verbindungen und Beschläge"), self.joints_panel, right
        )
        self.joints_panel.joint_created.connect(lambda _id: self.joints_dock.show())
        self.library_panel = LibraryPanel(self.controller.templates)
        self.library_dock = self._add_dock("library", self.tr("Möbel"), self.library_panel, left)
        self.library_panel.insert_requested.connect(self.insert_library_template)
        self.tree_panel.template_saved.connect(self.library_panel.reload)
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
        self.action_drawing = QAction(self.tr("&Zeichnung…"), self)
        self.action_drawing.setToolTip(self.tr("Technische Zeichnung als PDF oder DXF"))
        self.action_drawing.setShortcut(QKeySequence("Ctrl+P"))
        self.action_drawing.triggered.connect(self.show_drawing)
        self.action_labels = QAction(self.tr("&Etiketten…"), self)
        self.action_labels.setToolTip(self.tr("Je Teil ein Etikett mit Maßen und QR-Code (PDF)"))
        self.action_labels.triggered.connect(self.show_labels)
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
        self.action_add_board.setToolTip(self.tr("Neues Brett"))
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

        self.align_actions: list[tuple[QAction, int]] = []  # (action, minimum selection)
        for axis, mode, label in (
            (Axis.X, AlignMode.MIN, self.tr("Links bündig")),
            (Axis.X, AlignMode.CENTER, self.tr("Mittig (links/rechts)")),
            (Axis.X, AlignMode.MAX, self.tr("Rechts bündig")),
            (Axis.Y, AlignMode.MIN, self.tr("Vorne bündig")),
            (Axis.Y, AlignMode.CENTER, self.tr("Mittig (vorne/hinten)")),
            (Axis.Y, AlignMode.MAX, self.tr("Hinten bündig")),
            (Axis.Z, AlignMode.MIN, self.tr("Unten bündig")),
            (Axis.Z, AlignMode.CENTER, self.tr("Mittig (unten/oben)")),
            (Axis.Z, AlignMode.MAX, self.tr("Oben bündig")),
        ):
            action = QAction(label, self)
            action.setToolTip(self.tr("Am zuerst gewählten Teil ausrichten"))
            action.triggered.connect(partial(self._align, axis, mode))
            self.align_actions.append((action, 2))
        for axis, label in (
            (Axis.X, self.tr("Gleichmäßig verteilen (links/rechts)")),
            (Axis.Y, self.tr("Gleichmäßig verteilen (vorne/hinten)")),
            (Axis.Z, self.tr("Gleichmäßig verteilen (unten/oben)")),
        ):
            action = QAction(label, self)
            action.setToolTip(self.tr("Gleiche Abstände; die äußeren Teile bleiben stehen"))
            action.triggered.connect(partial(self._distribute, axis))
            self.align_actions.append((action, 3))

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

        self.action_rotate_mode = QAction(self.tr("&Drehen"), self)
        self.action_rotate_mode.setCheckable(True)
        self.action_rotate_mode.setShortcut(QKeySequence("R"))
        self.action_rotate_mode.setToolTip(
            self.tr("Drehringe statt Pfeilen: Teil frei im Raum drehen")
        )
        self.action_rotate_mode.toggled.connect(lambda on: setattr(self.scene, "rotate_mode", on))

        # Click tools (one at a time, Esc ends them)
        self.tool_actions = QActionGroup(self)
        self.tool_actions.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.action_insert_divider = self._tool_action(
            self.tr("&Trennwand einpassen"),
            "D",
            self.tr("Senkrechte Trennwand in ein Fach klicken"),
            lambda: InsertTool(self.scene, fit.InsertKind.DIVIDER),
        )
        self.action_insert_shelf = self._tool_action(
            self.tr("&Fachboden einpassen"),
            "F",
            self.tr("Fachboden in ein Fach klicken"),
            lambda: InsertTool(self.scene, fit.InsertKind.SHELF),
        )
        self.action_insert_back = self._tool_action(
            self.tr("&Rückwand einpassen"),
            "",
            self.tr("Rückwand in Nut: hinten in den Korpus klicken"),
            lambda: InsertTool(self.scene, fit.InsertKind.BACK),
        )
        self.action_insert_door = self._tool_action(
            self.tr("T&ür einpassen"),
            "Shift+T",
            self.tr(
                "Tür vor ein Fach klicken: aufliegend oder einliegend, ein- oder "
                "zweiflügelig, an das Fach gebunden"
            ),
            lambda: FrontTool(self.scene, front.FrontKind.DOOR),
        )
        self.action_insert_drawer = self._tool_action(
            self.tr("&Schublade einpassen"),
            "S",
            self.tr(
                "Schublade (Front, Zargen, Boden, Platz für Auszüge) in ein Fach "
                "klicken; mehrere übereinander mit 1–9"
            ),
            lambda: FrontTool(self.scene, front.FrontKind.DRAWER),
        )
        self.action_shelf_pins = self._tool_action(
            self.tr("&Lochreihe bohren"),
            "Shift+L",
            self.tr("Lochreihe für Bodenträger: auf die Innenseite einer Seitenwand klicken"),
            lambda: ShelfPinTool(self.scene),
        )
        self.action_measure = self._tool_action(
            self.tr("&Messen"),
            "M",
            self.tr("Abstand zwischen zwei parallelen Flächen messen"),
            lambda: FaceMeasureTool(self.scene),
        )
        self.action_measure_points = self._tool_action(
            self.tr("&Punkte messen"),
            "Shift+M",
            self.tr("Abstand zwischen zwei Punkten (Ecken, Kantenmitten) messen"),
            lambda: MeasureTool(self.scene),
        )
        self.action_set_distance = self._tool_action(
            self.tr("&Abstand setzen"),
            "E",
            self.tr(
                "Bezugsfläche klicken, dann die parallele Fläche des Teils, das "
                "verschoben wird, und den neuen Abstand tippen (einmalig, ohne Verknüpfung)"
            ),
            lambda: SetDistanceTool(self.scene),
        )
        self.scene.tool_changed.connect(self._sync_tool_actions)
        self.action_seat_groove = QAction(self.tr("In &Nut setzen…"), self)
        self.action_seat_groove.setToolTip(
            self.tr("Platte zwischen die Teile ringsum einpassen und mit Nuten verbinden")
        )
        self.action_seat_groove.triggered.connect(self.seat_in_groove)

        self.action_split = QAction(self.tr("An Auswahl &auftrennen"), self)
        self.action_split.setShortcut(QKeySequence("A"))
        self.action_split.setToolTip(
            self.tr(
                "Teile, durch die die Auswahl hindurchgeht, an ihr auftrennen "
                "(z. B. Fachböden an einer Trennwand). Die Auswahl bleibt ganz – "
                "Fachboden wählen, um die Trennwand aufzutrennen."
            )
        )
        self.action_split.triggered.connect(self.split_at_selection)

        self.action_array = QAction(self.tr("&Reihe kopieren…"), self)
        self.action_array.setToolTip(self.tr("Mehrere Kopien der Auswahl in einer Reihe"))
        self.action_array.triggered.connect(self.array_copy)
        self.action_isolate = QAction(self.tr("Auswahl &isolieren"), self)
        self.action_isolate.setCheckable(True)
        self.action_isolate.setShortcut(QKeySequence("I"))
        self.action_isolate.setToolTip(
            self.tr("Nur die Auswahl zeigen (noch einmal: wieder alles)")
        )
        self.action_isolate.toggled.connect(self._isolate)
        self.action_xray = QAction(self.tr("&Röntgenansicht"), self)
        self.action_xray.setCheckable(True)
        self.action_xray.setShortcut(QKeySequence("Alt+X"))
        self.action_xray.setToolTip(self.tr("Alle nicht ausgewählten Teile durchsichtig"))
        self.action_xray.toggled.connect(lambda on: setattr(self.scene, "xray", on))
        self.scene.view_mode_changed.connect(self._sync_view_modes)
        self.action_collisions = QAction(self.tr("&Kollisionen"), self)
        self.action_collisions.setCheckable(True)
        self.action_collisions.setShortcut(QKeySequence("K"))
        self.action_collisions.setToolTip(
            self.tr(
                "Überschneidende Teile rot anzeigen (Verbindungen wie Nut oder Zapfen zählen nicht)"
            )
        )
        self.action_collisions.toggled.connect(
            lambda on: setattr(self.scene, "show_collisions", on)
        )
        self.scene.collisions_changed.connect(self._report_collisions)

        self.action_fit = QAction(self.tr("Alles &zeigen"), self)
        self.action_fit.setShortcut(QKeySequence("F6"))
        self.action_fit.triggered.connect(self.viewport.fit_view)

        self.action_settings = QAction(self.tr("&Einstellungen…"), self)
        self.action_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.action_settings.triggered.connect(self.show_settings)
        self.action_shortcuts = QAction(self.tr("&Tastenkürzel…"), self)
        self.action_shortcuts.triggered.connect(self.show_shortcuts)

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
        file_menu.addAction(self.action_drawing)
        file_menu.addAction(self.cutlist_panel.export_action)
        file_menu.addAction(self.action_labels)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        edit_menu = menu_bar.addMenu(self.tr("&Bearbeiten"))
        edit_menu.addActions([self.action_undo, self.action_redo])
        edit_menu.addSeparator()
        tree = self.tree_panel
        edit_menu.addActions(
            [tree.action_rename, tree.action_duplicate, tree.action_delete, tree.action_group]
        )
        edit_menu.addAction(self.action_array)
        edit_menu.addAction(self.action_split)
        edit_menu.addAction(self.action_set_distance)

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
        insert_menu.addSeparator()
        insert_menu.addActions(
            [
                self.action_insert_divider,
                self.action_insert_shelf,
                self.action_insert_back,
                self.action_insert_door,
                self.action_insert_drawer,
            ]
        )
        insert_menu.addAction(self.action_seat_groove)
        insert_menu.addAction(self.action_split)

        self.arrange_menu = menu_bar.addMenu(self.tr("An&ordnen"))
        self._fill_arrange_menu(self.arrange_menu)

        joint_menu = menu_bar.addMenu(self.tr("&Verbindung"))
        joint_menu.addActions(list(self.joints_panel.add_actions.values()))
        joint_menu.addSeparator()
        joint_menu.addActions(list(self.joints_panel.fitting_actions.values()))
        joint_menu.addAction(self.action_shelf_pins)

        view_menu = menu_bar.addMenu(self.tr("&Ansicht"))
        view_menu.addActions(list(self.view_actions.values()))
        view_menu.addAction(self.action_fit)
        view_menu.addSeparator()
        view_menu.addActions([tree.action_toggle_visible, tree.action_transparent])
        view_menu.addAction(self.action_snap)
        view_menu.addAction(self.action_rotate_mode)
        view_menu.addActions([self.action_measure, self.action_measure_points])
        view_menu.addSeparator()
        view_menu.addActions([self.action_isolate, self.action_xray, self.action_collisions])
        view_menu.addSeparator()
        for dock in (
            self.library_dock,
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
        extras_menu.addSeparator()
        extras_menu.addAction(self.action_shortcuts)
        extras_menu.addAction(self.action_settings)

        help_menu = menu_bar.addMenu(self.tr("&Hilfe"))
        help_menu.addAction(self.action_about)

    def _create_toolbar(self) -> None:
        """Small titled tool groups in two rows; buttons show their shortcut."""
        groups: list[tuple[str, str, list[QAction]] | None] = [
            ("file", self.tr("Datei"), [self.action_new, self.action_open, self.action_save]),
            ("edit", self.tr("Bearbeiten"), [self.action_undo, self.action_redo]),
            (
                "insert",
                self.tr("Einfügen"),
                [
                    self.action_add_board,
                    self.action_add_sketch_part,
                    self.action_add_group,
                    self.action_add_parameter,
                    self.action_insert_template,
                ],
            ),
            ("check", self.tr("Prüfen"), [self.action_collisions]),
            (
                "tools",
                self.tr("Werkzeuge"),
                [self.action_rotate_mode, self.action_measure, self.action_set_distance],
            ),
            None,  # second row
            (
                "fit",
                self.tr("Einpassen"),
                [
                    self.action_insert_divider,
                    self.action_insert_shelf,
                    self.action_insert_back,
                    self.action_insert_door,
                    self.action_insert_drawer,
                    self.action_split,
                ],
            ),
            (
                "view",
                self.tr("Ansicht"),
                [self.action_isolate, self.action_xray, *self.view_actions.values()],
            ),
        ]
        # shorter button labels: the group title says the rest
        for action, short in (
            (self.action_insert_divider, self.tr("Trennwand")),
            (self.action_insert_shelf, self.tr("Fachboden")),
            (self.action_insert_back, self.tr("Rückwand")),
            (self.action_insert_door, self.tr("Tür")),
            (self.action_insert_drawer, self.tr("Schublade")),
            (self.action_split, self.tr("Auftrennen")),
            (self.action_isolate, self.tr("Isolieren")),
            (self.action_xray, self.tr("Röntgen")),
            (self.view_actions["iso"], self.tr("Iso")),
        ):
            action.setIconText(short)
        self.toolbars: dict[str, QToolBar] = {}
        for group in groups:
            if group is None:
                self.addToolBarBreak()
                continue
            name, title, actions = group
            toolbar = self.addToolBar(title)
            toolbar.setObjectName(f"toolbar_{name}")
            toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            label = QLabel(title)
            label.setObjectName("toolbar_title")
            label.setStyleSheet("color: gray; font-size: 8pt; padding: 0 4px;")
            toolbar.addWidget(label)
            for action in actions:
                toolbar.addAction(action)
                show_shortcut(action)
            self.toolbars[name] = toolbar
        # these icons speak for themselves (tooltip names them): room for more
        for name, action in (
            ("file", self.action_new),
            ("file", self.action_open),
            ("file", self.action_save),
            ("edit", self.action_undo),
            ("edit", self.action_redo),
        ):
            button = self.toolbars[name].widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

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

    def insert_template(self) -> InsertTemplateDialog:
        library = self.controller.templates
        dialog = InsertTemplateDialog(library, self, self._insert_from_dialog)
        if library.errors:
            QMessageBox.warning(
                self,
                self.tr("Vorlagen"),
                self.tr("Einige Vorlagen konnten nicht gelesen werden:\n{0}").format(
                    "\n".join(library.errors)
                ),
            )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def _insert_from_dialog(self, template: Template, parameters: dict[str, str]) -> None:
        first = not self.controller.doc.boards
        self.controller.insert_template(template, parameters)
        if first:
            self.viewport.fit_view()

    def insert_library_template(self, key: str) -> str | None:
        """Double-click in the furniture library: insert next to the existing parts."""
        template = self.library_panel.template(key)
        if template is None:
            return None
        first = not self.controller.doc.boards
        group = self.controller.insert_template(template)
        if first:
            self.viewport.fit_view()
        return group

    def drop_template(self, key: str, x: int, y: int) -> str | None:
        """A template was dragged from the library into the 3D view: place it
        on the floor under the cursor (on a 10 mm raster)."""
        template = self.library_panel.template(key)
        if template is None:
            return None
        point = self.scene.plane_point((0.0, 0.0, 0.0), 2, *self.viewport.to_display(x, y))
        position = None
        if point is not None:
            position = (round(point[0] / 10) * 10.0, round(point[1] / 10) * 10.0, 0.0)
        first = not self.controller.doc.boards
        group = self.controller.insert_template(template, position=position)
        if first:
            self.viewport.fit_view()
        self.activateWindow()
        self.viewport.setFocus()
        return group

    def show_view_menu(self, x: int, y: int) -> QMenu:
        """Context menu of the 3D view (right click)."""
        menu = QMenu(self)
        tree = self.tree_panel
        if self.controller.selection:
            menu.addActions([tree.action_rename, tree.action_duplicate, tree.action_delete])
            menu.addAction(self.action_array)
            menu.addAction(self.action_split)
            menu.addAction(self.action_isolate)
            menu.addSeparator()
            menu.addActions([tree.action_toggle_visible, tree.action_transparent])
            menu.addAction(tree.action_group)
            if len(self.controller.selection) >= 2:
                self._fill_arrange_menu(menu.addMenu(self.tr("Anordnen")))
            if self._single_board() is not None:
                menu.addAction(self.action_seat_groove)
            menu.addSeparator()
        menu.addAction(self.action_add_board)
        menu.addAction(self.action_insert_template)
        menu.addSeparator()
        menu.addAction(self.action_fit)
        views = menu.addMenu(self.tr("Ansicht"))
        views.addActions(list(self.view_actions.values()))
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(self.viewport.mapToGlobal(QPoint(x, y)))
        return menu

    def _tool_action(self, text: str, shortcut: str, tip: str, make: Callable[[], Tool]) -> QAction:
        action = QAction(text, self)
        action.setCheckable(True)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setToolTip(tip)
        self.tool_actions.addAction(action)

        def toggled(on: bool) -> None:
            if on:
                self.scene.start_tool(make())
                self.viewport.setFocus()
            elif self.scene.tool is not None and not any(
                a.isChecked() for a in self.tool_actions.actions()
            ):
                self.scene.end_tool()

        action.toggled.connect(toggled)
        return action

    def _sync_tool_actions(self) -> None:
        if self.scene.tool is None:
            for action in self.tool_actions.actions():
                action.blockSignals(True)
                action.setChecked(False)
                action.blockSignals(False)

    def _isolate(self, on: bool) -> None:
        if on == self.scene.isolated:
            return
        boards = self.scene.selected_boards() if on else None
        if on and not boards:
            self.statusBar().showMessage(self.tr("Zum Isolieren erst Teile auswählen"), 5000)
        self.scene.isolate(boards)

    def _report_collisions(self) -> None:
        if not self.scene.show_collisions:
            return
        found = self.scene.collisions
        if not found:
            self.statusBar().showMessage(self.tr("Keine Überschneidungen"), 5000)
            return
        boards = self.controller.doc.boards
        pairs = sorted({f"{boards[c.first].name} ↔ {boards[c.second].name}" for c in found})
        text = ", ".join(pairs[:4]) + (" …" if len(pairs) > 4 else "")
        self.statusBar().showMessage(
            self.tr("Überschneidungen ({0}): {1}").format(len(found), text)
        )

    def _sync_view_modes(self) -> None:
        for action, on in (
            (self.action_isolate, self.scene.isolated),
            (self.action_xray, self.scene.xray),
            (self.action_collisions, self.scene.show_collisions),
        ):
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)

    def array_copy(self) -> ArrayDialog | None:
        """Ask for count and spacing, then copy the selection in a row."""
        ids = list(self.controller.selection)
        box = self.scene.bounds(self.scene.selected_boards())
        if not ids or box is None:
            self.statusBar().showMessage(self.tr("Bitte zuerst Teile auswählen"), 5000)
            return None
        lo, hi = box
        dialog = ArrayDialog((hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]), self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(
            lambda: self.controller.array_copy(ids, dialog.count_spin.value(), dialog.step())
        )
        dialog.open()
        return dialog

    def split_at_selection(self) -> list[str]:
        """Split the parts the selected parts pass through."""
        cutters = [i for i in self.controller.selection if i in self.controller.doc.boards]
        if not cutters:
            self.statusBar().showMessage(self.tr("Bitte zuerst die trennenden Teile wählen"), 5000)
            return []
        pieces = self.controller.split_at(cutters)
        if pieces:
            text = self.tr("{n} neue Teilstücke").format(n=len(pieces))
        else:
            text = self.tr("Die Auswahl geht durch kein anderes Teil ganz hindurch")
        self.statusBar().showMessage(text, 5000)
        return pieces

    def _single_board(self) -> str | None:
        selection = self.controller.selection
        if len(selection) == 1 and selection[0] in self.controller.doc.boards:
            return selection[0]
        return None

    def seat_in_groove(self) -> GrooveDialog | None:
        """Ask for the groove and fit the selected panel into it."""
        board_id = self._single_board()
        if board_id is None:
            self.statusBar().showMessage(self.tr("Bitte genau eine Platte auswählen"), 5000)
            return None
        dialog = GrooveDialog(self.controller.groove, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def accepted() -> None:
            groove = dialog.settings()
            prefs = self.preferences
            prefs.groove_depth, prefs.groove_play = groove.depth, groove.play
            prefs.groove_distance = groove.distance
            prefs.save()
            self.controller.groove = groove
            if not self.controller.seat_in_groove(board_id, groove):
                self.statusBar().showMessage(
                    self.tr("Die Platte ist nicht ringsum von Teilen umgeben"), 5000
                )

        dialog.accepted.connect(accepted)
        dialog.open()
        return dialog

    def _set_snapping(self, enabled: bool) -> None:
        self.scene.snapping_enabled = enabled
        if hasattr(self, "preferences") and self.preferences.snapping != enabled:
            self.preferences.snapping = enabled
            self.preferences.save()

    def _fill_arrange_menu(self, menu: QMenu) -> None:
        for index, (action, _minimum) in enumerate(self.align_actions):
            if index and index % 3 == 0:
                menu.addSeparator()
            menu.addAction(action)

    def _align(self, axis: Axis, mode: AlignMode) -> None:
        self.controller.align(self.controller.selection, axis, mode)

    def _distribute(self, axis: Axis) -> None:
        self.controller.distribute(self.controller.selection, axis)

    def _update_selection_info(self) -> None:
        """Arrange actions and the size of the selection (measuring)."""
        selection = self.controller.selection
        for action, minimum in self.align_actions:
            action.setEnabled(len(selection) >= minimum)
        doc, evaluation = self.controller.doc, self.controller.evaluation
        box = arrange.bounds(doc, selection, evaluation) if selection else None
        if box is None:
            self.selection_info.clear()
            return
        size = " × ".join(format_number(box[1][i] - box[0][i]) for i in range(3))
        text = self.tr("Auswahl: {0} mm").format(size)
        if len(selection) == 2:
            gaps = arrange.gaps(doc, selection[0], selection[1], evaluation)
            if gaps is not None:
                parts = [
                    f"{name} {format_number(gap)}"
                    for name, gap in zip("XYZ", gaps, strict=True)
                    if gap >= 0
                ]
                if parts:
                    text += self.tr(" · Abstand: {0} mm").format(", ".join(parts))
        self.selection_info.setText(text)

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

    def show_drawing(self) -> DrawingDialog:
        dialog = DrawingDialog(self.controller, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def show_labels(self) -> LabelsDialog:
        dialog = LabelsDialog(self.controller, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

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
    # ------------------------------------------------------------------ preferences
    def named_actions(self) -> dict[str, QAction]:
        actions = {
            name: value
            for name, value in vars(self).items()
            if name.startswith("action_") and isinstance(value, QAction)
        }
        actions.update({f"view_{name}": action for name, action in self.view_actions.items()})
        return actions

    def _init_shortcuts(self) -> None:
        actions = self.named_actions()
        self.default_shortcuts = {n: a.shortcut().toString() for n, a in actions.items()}
        apply_saved_shortcuts(actions)

    def apply_preferences(self, prefs: Preferences) -> None:
        self.preferences = prefs
        self.action_snap.setChecked(prefs.snapping)
        self.scene.snap_tolerance_px = prefs.snap_tolerance_px
        self.controller.set_autosave_minutes(prefs.autosave_minutes)
        self.controller.new_board_size = (
            f"{prefs.board_length:g}",
            f"{prefs.board_width:g}",
            f"{prefs.board_thickness:g}",
        )
        self.controller.groove = fit.GrooveSettings(
            prefs.groove_depth, prefs.groove_play, prefs.groove_distance
        )

    def show_settings(self) -> SettingsDialog:
        dialog = SettingsDialog(self.preferences, self)

        def accepted() -> None:
            prefs = dialog.preferences()
            prefs.save()
            self.apply_preferences(prefs)

        dialog.accepted.connect(accepted)
        dialog.open()
        return dialog

    def show_shortcuts(self) -> ShortcutsDialog:
        dialog = ShortcutsDialog(self.named_actions(), self.default_shortcuts, self)
        dialog.open()
        return dialog

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
