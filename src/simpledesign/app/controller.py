"""The controller owns the document and is the *only* place that changes it.

Every change runs through :meth:`Controller.execute`:

1. the change is applied to a *copy* of the document - if it raises (bad
   formula, duplicate name …) the real document is untouched;
2. a snapshot before and after is pushed onto the undo stack;
3. everything is re-evaluated and ``document_changed`` is emitted.

Snapshots (the ``.sdz`` dict) make undo trivially correct for every kind of
change. A document of a few hundred parts is a few hundred kB - no problem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar, override

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from simpledesign.core.io import sdz
from simpledesign.core.materials.library import LibraryError, MaterialLibrary
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.part import Axis, Orientation
from simpledesign.core.model.settings import CutSettings
from simpledesign.core.params.errors import ErrorCode
from simpledesign.core.params.expression import format_number
from simpledesign.core.templates import template as tpl
from simpledesign.core.templates.library import TemplateLibrary

T = TypeVar("T")

AUTOSAVE_INTERVAL_MS = 120_000
RECOVERY_FILE = "recovery.sdz"
LIBRARY_FILE = "materials.json"
TEMPLATES_FOLDER = "templates"
# Gap between furniture placed next to each other (inserting a template).
PLACEMENT_GAP = 200.0
_MERGE_COMMAND_ID = 1


class SnapshotCommand(QUndoCommand):
    def __init__(
        self,
        controller: Controller,
        text: str,
        before: sdz.Json,
        after: sdz.Json,
        merge_key: str | None,
    ) -> None:
        super().__init__(text)
        self._controller = controller
        self.before = before
        self.after = after
        self.merge_key = merge_key
        self._applied = True  # the change is already done when pushed

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            return
        self._controller.restore_snapshot(self.after)

    def undo(self) -> None:
        self._controller.restore_snapshot(self.before)

    def id(self) -> int:
        return _MERGE_COMMAND_ID if self.merge_key else -1

    @override
    def mergeWith(self, other: QUndoCommand) -> bool:
        if isinstance(other, SnapshotCommand) and other.merge_key == self.merge_key:
            self.after = other.after
            return True
        return False


def default_recovery_dir() -> Path:
    location = QStandardPaths.StandardLocation.AppDataLocation
    return Path(QStandardPaths.writableLocation(location))


class Controller(QObject):
    document_changed = Signal()
    selection_changed = Signal()
    file_state_changed = Signal()
    library_changed = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        recovery_dir: Path | None = None,
        library_path: Path | None = None,
        templates_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.undo_stack = QUndoStack(self)
        self.undo_stack.cleanChanged.connect(self.file_state_changed)
        self.doc = Document()
        self.evaluator = Evaluator()
        self.evaluation: Evaluation = self.evaluator.evaluate(self.doc)
        self.selection: list[str] = []
        self.file_path: Path | None = None
        self.recovery_path = (recovery_dir or default_recovery_dir()) / RECOVERY_FILE

        # The user's material library (shared by all documents). A broken file
        # is not overwritten until the user saves the library again.
        self.library_path = library_path or default_recovery_dir() / LIBRARY_FILE
        self.library_error: str | None = None
        try:
            self.library = MaterialLibrary.load(self.library_path)
        except LibraryError as error:
            self.library = MaterialLibrary()
            self.library_error = str(error)

        base = recovery_dir or default_recovery_dir()
        self.templates = TemplateLibrary(templates_dir or base / TEMPLATES_FOLDER)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

    # ================================================================== state
    @property
    def modified(self) -> bool:
        return not self.undo_stack.isClean()

    def _refresh(self) -> None:
        self.evaluation = self.evaluator.evaluate(self.doc)
        self.document_changed.emit()

    def _replace_document(self, doc: Document, path: Path | None) -> None:
        self.doc = doc
        self.evaluator = Evaluator()
        self.file_path = path
        self.undo_stack.clear()
        self.selection = []
        self._refresh()
        self.selection_changed.emit()
        self.file_state_changed.emit()

    def restore_snapshot(self, snapshot: sdz.Json) -> None:
        self.doc = sdz.from_dict(snapshot)
        self._refresh()
        kept = [i for i in self.selection if self._exists(i)]
        if kept != self.selection:
            self.selection = kept
            self.selection_changed.emit()

    def _exists(self, obj_id: str) -> bool:
        return obj_id in self.doc.boards or obj_id in self.doc.groups

    # ================================================================== changes
    def execute(
        self, text: str, change: Callable[[Document], T], merge_key: str | None = None
    ) -> T:
        """Apply ``change`` as one undoable step. Exceptions leave the document as it was."""
        before = sdz.to_dict(self.doc)
        work = sdz.from_dict(before)
        result = change(work)
        after = sdz.to_dict(work)
        if after != before:
            self.doc = work
            self.undo_stack.push(SnapshotCommand(self, text, before, after, merge_key))
            self._refresh()
        return result

    def set_selection(self, ids: Iterable[str]) -> None:
        ids = [i for i in dict.fromkeys(ids) if self._exists(i)]
        if ids != self.selection:
            self.selection = ids
            self.selection_changed.emit()

    def set_expression(self, owner_id: str, prop: str | None, text: str) -> None:
        self.execute(self.tr("Formel ändern"), lambda d: d.set_expression(owner_id, prop, text))

    def rename(self, obj_id: str, name: str) -> None:
        self.execute(self.tr("Umbenennen"), lambda d: d.rename(obj_id, name.strip()))

    def add_board(self, group_id: str | None = None) -> str:
        board_id = self.execute(
            self.tr("Brett hinzufügen"),
            lambda d: d.add_board(group_id=group_id, material_id=self.library.default()).id,
        )
        self.set_selection([board_id])
        return board_id

    def add_group(self, parent_id: str | None = None, members: Iterable[str] = ()) -> str:
        members = list(members)

        def change(d: Document) -> str:
            group = d.add_group(parent_id=parent_id)
            if members:
                d.move_to_group(members, group.id)
            return group.id

        group_id = self.execute(self.tr("Gruppe hinzufügen"), change)
        self.set_selection([group_id])
        return group_id

    def add_parameter(self, group_id: str | None = None) -> str:
        def change(d: Document) -> str:
            name = d.unique_name("p_1", is_parameter=True, group_id=group_id)
            return d.add_parameter(name, "100", group_id).id

        return self.execute(self.tr("Parameter hinzufügen"), change)

    def set_material(self, board_ids: Iterable[str], material_id: str) -> None:
        """Assign a material from the library (or one only the document has)."""
        ids = list(board_ids)
        material = self.library.get(material_id) or material_id

        def change(d: Document) -> None:
            for board_id in ids:
                d.set_material(board_id, material)

        self.execute(self.tr("Material ändern"), change)

    def set_ignore_grain(self, board_ids: Iterable[str], ignore: bool) -> None:
        ids = [i for i in board_ids if i in self.doc.boards]

        def change(d: Document) -> None:
            for board_id in ids:
                d.boards[board_id].ignore_grain = ignore

        self.execute(self.tr("Faserrichtung ändern"), change)

    # ------------------------------------------------------------------ joints
    def add_joint(self, kind: JointKind, main_id: str, attached_id: str) -> str:
        return self.execute(
            self.tr("Verbindung anlegen"), lambda d: d.add_joint(kind, main_id, attached_id).id
        )

    def set_joint_param(self, joint_id: str, name: str, value: float) -> None:
        self.execute(
            self.tr("Verbindung ändern"),
            lambda d: d.set_joint_param(joint_id, name, value),
            merge_key=f"joint:{joint_id}:{name}",
        )

    def swap_joint(self, joint_id: str) -> None:
        self.execute(self.tr("Haupt-/Anschlussteil tauschen"), lambda d: d.swap_joint(joint_id))

    def delete_joints(self, joint_ids: Iterable[str]) -> None:
        ids = [i for i in joint_ids if i in self.doc.joints]

        def change(d: Document) -> None:
            for joint_id in ids:
                del d.joints[joint_id]

        self.execute(self.tr("Verbindung löschen"), change)

    def set_cut_settings(self, settings: CutSettings) -> None:
        def change(d: Document) -> None:
            d.cut_settings = settings

        self.execute(self.tr("Zuschnitt-Einstellungen ändern"), change, merge_key="cut_settings")

    # ================================================================== library
    def set_library(self, library: MaterialLibrary) -> None:
        """Store a new library. Raises OSError if it cannot be saved."""
        library.save(self.library_path)
        self.library = library
        self.library_error = None
        self.library_changed.emit()

    def outdated_materials(self) -> list[str]:
        """Document materials whose library version differs from the document copy."""
        return [
            material_id
            for material_id, material in self.doc.materials.items()
            if (library_version := self.library.get(material_id)) is not None
            and library_version != material
        ]

    def update_materials(self, material_ids: Iterable[str]) -> None:
        """Replace the document copies by the current library versions."""
        materials = [
            m
            for i in material_ids
            if (m := self.library.get(i)) is not None and i in self.doc.materials
        ]

        def change(d: Document) -> None:
            for material in materials:
                d.update_material(material)

        self.execute(self.tr("Material aktualisieren"), change)

    def set_orientation(self, board_id: str, orientation: Orientation) -> None:
        def change(d: Document) -> None:
            d.boards[board_id].orientation = orientation

        self.execute(self.tr("Lage ändern"), change)

    def set_pattern_axis(self, board_id: str, axis: Axis) -> None:
        def change(d: Document) -> None:
            d.boards[board_id].pattern_axis = axis

        self.execute(self.tr("Anordnung ändern"), change)

    def set_visible(self, ids: Iterable[str], visible: bool) -> None:
        ids = list(ids)

        def change(d: Document) -> None:
            for obj_id in ids:
                (d.boards.get(obj_id) or d.groups[obj_id]).visible = visible

        self.execute(self.tr("Einblenden") if visible else self.tr("Ausblenden"), change)

    def set_transparent(self, board_ids: Iterable[str], transparent: bool) -> None:
        ids = [i for i in board_ids if i in self.doc.boards]

        def change(d: Document) -> None:
            for board_id in ids:
                d.boards[board_id].transparent = transparent

        self.execute(self.tr("Transparenz ändern"), change)

    def move_to_group(self, ids: Iterable[str], group_id: str | None) -> None:
        ids = list(ids)
        self.execute(self.tr("In Gruppe verschieben"), lambda d: d.move_to_group(ids, group_id))

    def duplicate(self, ids: Iterable[str]) -> list[str]:
        ids = list(ids)
        copies = self.execute(self.tr("Duplizieren"), lambda d: d.duplicate(ids))
        self.set_selection(copies)
        return copies

    def delete(self, ids: Iterable[str]) -> None:
        """Delete; formulas elsewhere that use the deleted objects get fixed values."""
        ids = list(ids)
        values = dict(self.evaluation.values)
        self.execute(self.tr("Löschen"), lambda d: d.delete(ids, values))
        self.set_selection(i for i in self.selection if i not in ids)

    def move_board(self, board_id: str, axis: Axis, formula: str, value: float) -> None:
        """Set a position formula from the gizmo.

        A snap formula can create a cycle (the target already depends on this
        board) - then the plain ``value`` is used instead.
        """

        def change(d: Document) -> None:
            d.set_expression(board_id, axis.value, formula)
            error = Evaluator().evaluate(d).error(board_id, axis.value)
            if error is not None and error.code == ErrorCode.CYCLE:
                d.set_expression(board_id, axis.value, format_number(value))

        self.execute(self.tr("Verschieben"), change)

    # ================================================================== templates
    def free_position(self) -> tuple[float, float, float]:
        """Where new furniture goes: right of everything that exists."""
        right = [
            origin[0] + state.size[0]
            for state in self.evaluation.boards.values()
            for origin in state.instance_origins()
        ]
        return (max(right) + PLACEMENT_GAP, 0.0, 0.0) if right else (0.0, 0.0, 0.0)

    def insert_template(
        self,
        template: tpl.Template,
        parameters: dict[str, str] | None = None,
        parent_id: str | None = None,
    ) -> str:
        """Insert a template as a new group (right of the existing parts) and select it."""
        offset = self.free_position()
        group_id = self.execute(
            self.tr("Vorlage einfügen"),
            lambda d: tpl.insert(d, template, parent_id, parameters, offset),
        )
        self.set_selection([group_id])
        return group_id

    def save_group_as_template(self, group_id: str, name: str, description: str = "") -> Path:
        values = dict(self.evaluation.values)
        template = tpl.from_group(self.doc, group_id, values, name, description)
        return self.templates.add(template)

    # ================================================================== files
    def new_document(self) -> None:
        self._replace_document(Document(), None)

    def open(self, path: Path) -> None:
        """Raises :class:`sdz.FileFormatError` or :class:`OSError`."""
        self._replace_document(sdz.load(path), path)

    def save(self, path: Path) -> None:
        sdz.save(self.doc, path)
        self.file_path = path
        self.undo_stack.setClean()
        self.discard_recovery()
        self.file_state_changed.emit()

    # ================================================================== autosave
    def autosave(self) -> None:
        if not self.modified:
            return
        self.recovery_path.parent.mkdir(parents=True, exist_ok=True)
        source = str(self.file_path) if self.file_path else None
        sdz.save(self.doc, self.recovery_path, extra={"source_path": source})

    def recovery_available(self) -> bool:
        return self.recovery_path.exists()

    def restore_recovery(self) -> None:
        data = sdz.load_json(self.recovery_path)
        source = data.pop("source_path", None)
        self._replace_document(sdz.from_dict(data), Path(source) if source else None)
        self.undo_stack.resetClean()  # restored work is unsaved
        self.file_state_changed.emit()

    def discard_recovery(self) -> None:
        self.recovery_path.unlink(missing_ok=True)
