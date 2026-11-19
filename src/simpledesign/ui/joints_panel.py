"""Joints (Verbindungen) and fittings (Beschläge): create from the selection,
list, edit settings.

A joint is created from exactly two selected boards: the first selected is
the main part (Hauptteil), the second the attached part (Anschlussteil).
A hinge or slide likewise: first the door / drawer side, then the carcass
side that holds it. Shelf pin rows come from the "Lochreihe" tool.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.core.model.fitting import FITTING_DEFAULTS, FittingKind
from simpledesign.core.model.joint import JOINT_DEFAULTS, JointKind
from simpledesign.ui.texts import (
    fitting_kind_label,
    fitting_param_label,
    joint_error_message,
    joint_kind_label,
    joint_param_label,
    side_label,
)

JOINT_ID = Qt.ItemDataRole.UserRole


def _spin(plain: bool) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, 10000)
    spin.setDecimals(0 if plain else 1)
    spin.setSingleStep(1 if plain else 0.5)
    spin.setSuffix("" if plain else " mm")
    spin.setKeyboardTracking(False)
    return spin


class JointsPanel(QWidget):
    joint_created = Signal(str)

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._updating = False

        self.add_actions: dict[JointKind, QAction] = {}
        for kind in JointKind:
            action = QAction(joint_kind_label(kind), self)
            action.setToolTip(
                self.tr(
                    "{kind}-Verbindung zwischen zwei gewählten Brettern "
                    "(das zuerst gewählte ist das Hauptteil)"
                ).format(kind=joint_kind_label(kind))
            )
            action.triggered.connect(lambda _checked=False, k=kind: self.add_joint(k))
            self.add_actions[kind] = action
        self.fitting_actions: dict[FittingKind, QAction] = {}
        tips = {
            FittingKind.HINGE: self.tr(
                "Topfbänder: zuerst die Tür wählen, dann die Seite, an der sie angeschlagen ist"
            ),
            FittingKind.SLIDE: self.tr(
                "Schubkastenschiene: zuerst die Zarge wählen, dann die Korpusseite daneben"
            ),
        }
        for fitting_kind, tip in tips.items():
            action = QAction(fitting_kind_label(fitting_kind), self)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, k=fitting_kind: self.add_fitting(k))
            self.fitting_actions[fitting_kind] = action

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._show_current)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b;")
        self.parts_label = QLabel()
        self.parts_label.setWordWrap(True)

        self.form = QFormLayout()
        self.spins: dict[str, QDoubleSpinBox] = {}
        names = dict.fromkeys(name for params in JOINT_DEFAULTS.values() for name in params)
        for name in names:
            spin = _spin(name in ("count", "slope"))  # numbers without a unit
            spin.valueChanged.connect(lambda value, n=name: self._set_param(n, value))
            self.form.addRow(joint_param_label(name), spin)
            self.spins[name] = spin
        self.fitting_form = QFormLayout()
        self.fitting_spins: dict[str, QDoubleSpinBox] = {}
        names = dict.fromkeys(name for params in FITTING_DEFAULTS.values() for name in params)
        for name in names:
            spin = _spin(name == "count")
            spin.valueChanged.connect(lambda value, n=name: self._set_fitting_param(n, value))
            self.fitting_form.addRow(fitting_param_label(name), spin)
            self.fitting_spins[name] = spin

        self.swap_button = QPushButton(self.tr("Haupt-/Anschlussteil tauschen"))
        self.swap_button.clicked.connect(self.swap_current)
        self.delete_button = QPushButton(self.tr("Löschen"))
        self.delete_button.clicked.connect(self.delete_current)
        buttons = QHBoxLayout()
        buttons.addWidget(self.swap_button)
        buttons.addWidget(self.delete_button)

        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.addWidget(self.parts_label)
        details.addWidget(self.error_label)
        details.addLayout(self.form)
        details.addLayout(self.fitting_form)
        details.addLayout(buttons)

        self.hint = QLabel(
            self.tr(
                "Zwei Bretter wählen und im Menü „Verbindung“ eine Verbindung oder "
                "einen Beschlag anlegen."
            )
        )
        self.hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.hint)
        layout.addWidget(self.details)

        controller.document_changed.connect(self.refresh)
        controller.selection_changed.connect(self._update_actions)
        self.refresh()
        self._update_actions()

    # ------------------------------------------------------------------ state
    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return None if item is None else str(item.data(JOINT_ID))

    def select_joint(self, joint_id: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(JOINT_ID) == joint_id:
                self.list.setCurrentRow(row)
                return

    def _selected_boards(self) -> list[str]:
        return [i for i in self.controller.selection if i in self.controller.doc.boards]

    def _update_actions(self) -> None:
        enabled = len(self._selected_boards()) == 2
        for action in [*self.add_actions.values(), *self.fitting_actions.values()]:
            action.setEnabled(enabled)

    def refresh(self) -> None:
        doc = self.controller.doc
        errors = self.controller.evaluation.machining.errors
        current = self.current_id()
        self.list.blockSignals(True)
        self.list.clear()
        entries: list[tuple[str, str]] = []
        for joint in doc.joints.values():
            text = self.tr("{kind}: {main} → {attached}").format(
                kind=joint_kind_label(joint.kind),
                main=doc.name_of(joint.main_id),
                attached=doc.name_of(joint.attached_id),
            )
            entries.append((joint.id, text))
        for fitting in doc.fittings.values():
            if fitting.carrier_id is None:
                axis = doc.boards[fitting.part_id].orientation.thickness_axis
                where = side_label(axis, fitting.side)
                text = self.tr("{kind}: {part}, {side}").format(
                    kind=fitting_kind_label(fitting.kind),
                    part=doc.name_of(fitting.part_id),
                    side=where,
                )
            else:
                text = self.tr("{kind}: {part} an {carrier}").format(
                    kind=fitting_kind_label(fitting.kind),
                    part=doc.name_of(fitting.part_id),
                    carrier=doc.name_of(fitting.carrier_id),
                )
            entries.append((fitting.id, text))
        for entry_id, text in entries:
            item = QListWidgetItem(("⚠ " if entry_id in errors else "") + text)
            item.setData(JOINT_ID, entry_id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if current is not None:
            self.select_joint(current)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._show_current()

    def _show_current(self, *_args: object) -> None:
        entry_id = self.current_id()
        doc = self.controller.doc
        joint = doc.joints.get(entry_id) if entry_id else None
        fitting = doc.fittings.get(entry_id) if entry_id else None
        self.details.setVisible(joint is not None or fitting is not None)
        self.hint.setVisible(not doc.joints and not doc.fittings)
        if entry_id is None or (joint is None and fitting is None):
            return
        error = self.controller.evaluation.machining.errors.get(entry_id)
        self.error_label.setText(joint_error_message(error) if error else "")
        self.error_label.setVisible(error is not None)
        self.swap_button.setVisible(joint is not None)
        self._updating = True
        for name, spin in self.spins.items():
            used = joint is not None and name in JOINT_DEFAULTS[joint.kind]
            self.form.setRowVisible(spin, used)
            if joint is not None and used:
                spin.setValue(joint.param(name))
        for name, spin in self.fitting_spins.items():
            used = fitting is not None and name in FITTING_DEFAULTS[fitting.kind]
            self.fitting_form.setRowVisible(spin, used)
            if fitting is not None and used:
                spin.setValue(fitting.param(name))
        self._updating = False
        if joint is not None:
            self.parts_label.setText(
                self.tr("Hauptteil: {main}\nAnschlussteil: {attached}").format(
                    main=doc.name_of(joint.main_id), attached=doc.name_of(joint.attached_id)
                )
            )
        elif fitting is not None and fitting.carrier_id is not None:
            self.parts_label.setText(
                self.tr("Teil: {part}\nTräger: {carrier}").format(
                    part=doc.name_of(fitting.part_id), carrier=doc.name_of(fitting.carrier_id)
                )
            )
        elif fitting is not None:
            self.parts_label.setText(
                self.tr("Teil: {part}").format(part=doc.name_of(fitting.part_id))
            )

    # ------------------------------------------------------------------ actions
    def add_joint(self, kind: JointKind) -> str | None:
        boards = self._selected_boards()
        if len(boards) != 2:
            return None
        joint_id = self.controller.add_joint(kind, boards[0], boards[1])
        self.select_joint(joint_id)
        self.joint_created.emit(joint_id)
        return joint_id

    def add_fitting(self, kind: FittingKind) -> str | None:
        boards = self._selected_boards()
        if len(boards) != 2:
            return None
        fitting_id = self.controller.add_fitting(kind, boards[0], boards[1])
        self.select_joint(fitting_id)
        self.joint_created.emit(fitting_id)
        return fitting_id

    def _set_param(self, name: str, value: float) -> None:
        joint_id = self.current_id()
        if self._updating or joint_id is None or joint_id not in self.controller.doc.joints:
            return
        self.controller.set_joint_param(joint_id, name, value)

    def _set_fitting_param(self, name: str, value: float) -> None:
        fitting_id = self.current_id()
        if self._updating or fitting_id is None or fitting_id not in self.controller.doc.fittings:
            return
        self.controller.set_fitting_param(fitting_id, name, value)

    def swap_current(self) -> None:
        joint_id = self.current_id()
        if joint_id is not None and joint_id in self.controller.doc.joints:
            self.controller.swap_joint(joint_id)

    def delete_current(self) -> None:
        entry_id = self.current_id()
        if entry_id is None:
            return
        if entry_id in self.controller.doc.fittings:
            self.controller.delete_fittings([entry_id])
        else:
            self.controller.delete_joints([entry_id])
