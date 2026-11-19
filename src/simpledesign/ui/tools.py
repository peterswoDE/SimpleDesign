"""Click tools of the 3D view: fit a new board into a compartment, measure
points or faces, set the distance between two faces.

A tool takes over the pointer events of the view while it is active (the
scene hands them back when the tool ends: Esc, right click).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pyvista as pv
from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersCore import vtkAppendPolyData
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from simpledesign.app.snapping import Vec3
from simpledesign.core.materials.library import builtin_material
from simpledesign.core.model import fit, front, transform
from simpledesign.core.model.fitting import Fitting, FittingKind
from simpledesign.core.model.machining import fitting_holes
from simpledesign.core.model.part import Axis
from simpledesign.ui.texts import number

if TYPE_CHECKING:
    from simpledesign.ui.scene import Scene

BACK_MATERIAL = "builtin.hdf_3"
PREVIEW_COLOR = (0.25, 0.75, 0.35)
PREVIEW_OPACITY = 0.45
CENTRE_SNAP_PX = 12
MEASURE_SNAP_PX = 12


def _tr(text: str) -> str:
    return QCoreApplication.translate("Tools", text)


def _box_actor() -> vtkActor:
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(pv.Box(bounds=(0.0, 1.0, 0.0, 1.0, 0.0, 1.0)))
    actor = vtkActor()
    actor.SetMapper(mapper)
    prop = actor.GetProperty()
    prop.SetColor(*PREVIEW_COLOR)
    prop.SetOpacity(PREVIEW_OPACITY)
    prop.SetAmbient(0.5)
    actor.PickableOff()
    actor.VisibilityOff()
    return actor


class Tool:
    """Base: ignores everything, ends on Esc and right click."""

    def __init__(self, scene: Scene) -> None:
        self.scene = scene

    def start(self) -> None: ...

    def finish(self) -> None: ...

    def title(self) -> str:
        """Heading of the tool panel."""
        return _tr("Werkzeug")

    def options_widget(self) -> QWidget | None:
        """Settings shown in the tool panel (None: only the hint)."""
        return None

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        return False

    def move(self, x: int, y: int) -> bool:
        return False

    def release(self, x: int, y: int, ctrl: bool) -> None: ...

    def hover(self, x: int, y: int) -> None: ...

    def context_menu(self, x: int, y: int) -> None:
        self.scene.end_tool()

    def cancel(self) -> None:
        self.scene.end_tool()

    def type_key(self, key: int, text: str) -> bool:
        return False

    def scene_rebuilt(self) -> None:
        """The scene made its actors anew (document or selection changed)."""


class InsertTool(Tool):
    """Hover a compartment: a new board fills it (preview); a click adds it.

    Divider and shelf centre on the cursor and snap to the middle; a typed
    number is the clear distance from the lower / left face.
    """

    def __init__(self, scene: Scene, kind: fit.InsertKind) -> None:
        super().__init__(scene)
        self.kind = kind
        self.plan: fit.InsertPlan | None = None
        self.material_id = ""
        self.thickness_formula = ""
        self.typed = ""
        self._last: tuple[int, int] | None = None
        self.preview = _box_actor()

    def title(self) -> str:
        return _tr("{0} einpassen").format(_tr(fit.BASE_NAMES[self.kind]))

    def start(self) -> None:
        self.scene.renderer.AddActor(self.preview)
        self._hint()

    def finish(self) -> None:
        self.scene.renderer.RemoveActor(self.preview)
        self.scene.viewport.render()

    def _hint(self) -> None:
        name = _tr(fit.BASE_NAMES[self.kind])
        self.scene.status_message.emit(
            _tr("{0} einpassen: in ein Fach zeigen und klicken   (Esc: beenden)").format(name)
        )

    # ------------------------------------------------------------------ planning
    def _source(self, comp: fit.Compartment, hit_board: str) -> tuple[str, str, float]:
        """Material, thickness formula and thickness for the new board."""
        controller = self.scene.controller
        doc, evaluation = controller.doc, controller.evaluation
        if self.kind == fit.InsertKind.BACK:
            thickness = builtin_material(BACK_MATERIAL).thickness
            return BACK_MATERIAL, number(thickness), thickness
        # the carcass parts around, not a thin back panel the cursor is on
        around = sorted(comp.board_ids()) or [hit_board]
        board_id = max(around, key=lambda b: (evaluation.boards[b].thickness, b == hit_board))
        thickness = evaluation.boards[board_id].thickness
        formula = doc.display_expression(board_id, "thickness")
        return doc.boards[board_id].material_id, formula, thickness

    def plan_at(self, x: int, y: int) -> fit.InsertPlan | None:
        scene = self.scene
        hit = scene.pick_face(x, y)
        if hit is None:
            return None
        board_id, point, normal, sign = hit
        controller = scene.controller
        doc, evaluation = controller.doc, controller.evaluation
        comp = fit.compartment(doc, evaluation, point, normal, sign)
        if comp is None:
            return None
        material_id, formula, thickness = self._source(comp, board_id)
        groove = controller.groove
        if self.kind == fit.InsertKind.BACK:
            comp = fit.back_compartment(doc, evaluation, comp, point, thickness, groove)
            if comp is None:
                return None
        view = scene.renderer.GetActiveCamera().GetDirectionOfProjection()
        thickness_axis = 0 if abs(view[0]) < abs(view[1]) else 1
        cursor_axis = 2 if self.kind == fit.InsertKind.SHELF else thickness_axis
        snap = CENTRE_SNAP_PX * scene.world_per_pixel(point)
        typed = self._typed_value()
        plan = fit.plan_insert(
            doc,
            evaluation,
            comp,
            self.kind,
            thickness_axis,
            point[cursor_axis],
            thickness,
            snap=0.0 if scene_shift(scene) else snap,
            typed=typed,
            groove=groove,
        )
        self.material_id, self.thickness_formula = material_id, formula
        return plan

    def _typed_value(self) -> float | None:
        try:
            return float(self.typed)
        except ValueError:
            return None

    def _show(self, plan: fit.InsertPlan | None) -> None:
        self.plan = plan
        actor = self.preview
        actor.SetVisibility(plan is not None)
        if plan is None:
            self._hint()
        else:
            actor.SetPosition(*plan.lo)
            actor.SetScale(*(max(v, 0.01) for v in plan.size))
            self._status(plan)
        self.scene.viewport.render()

    def _status(self, plan: fit.InsertPlan) -> None:
        name = _tr(fit.BASE_NAMES[self.kind])
        if self.kind == fit.InsertKind.BACK:
            where = _tr("{0} mm von hinten, Nut {1} mm").format(
                number(self.scene.controller.groove.distance),
                number(self.scene.controller.groove.depth),
            )
        elif self.typed:
            where = _tr("Abstand {0} mm (getippt)").format(self.typed)
        elif plan.centred:
            where = _tr("mittig")
        else:
            where = _tr("Abstand {0} mm").format(number(plan.gap))
        size = " × ".join(number(v) for v in plan.size)
        self.scene.status_message.emit(
            _tr(
                "{0}: {1}, {2} mm   (Klick: einfügen, Zahl tippen: Abstand, "
                "Umschalt: frei, Esc: beenden)"
            ).format(name, where, size)
        )

    # ------------------------------------------------------------------ events
    def hover(self, x: int, y: int) -> None:
        self._last = (x, y)
        self._show(self.plan_at(x, y))

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        plan = self.plan_at(x, y)
        if plan is None:
            return False  # empty space: orbit
        self._place(plan)
        return True

    def _place(self, plan: fit.InsertPlan) -> None:
        scene = self.scene
        scene.controller.insert_board(plan, self.material_id, self.thickness_formula)
        self.typed = ""
        self._show(None)

    def type_key(self, key: int, text: str) -> bool:
        if self.kind == fit.InsertKind.BACK:
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.plan is not None:
                self._place(self.plan)
            return True
        if key == Qt.Key.Key_Backspace:
            self.typed = self.typed[:-1]
        elif text and text in "0123456789.,":
            self.typed += "." if text == "," else text
        else:
            return False
        if self._last is not None:
            self._show(self.plan_at(*self._last))
        return True


class FrontTool(Tool):
    """Point into a compartment: a door or drawer closes its front (preview);
    a click adds it. Keys change the settings while the tool runs."""

    def __init__(self, scene: Scene, kind: front.FrontKind) -> None:
        super().__init__(scene)
        self.kind = kind
        self.plan: front.FrontPlan | None = None
        self._last: tuple[int, int] | None = None
        self.preview = _box_actor()
        self.options: _FrontOptions | None = None

    @property
    def settings(self) -> front.FrontSettings:
        return self.scene.controller.front_settings

    def title(self) -> str:
        if self.kind == front.FrontKind.DOOR:
            return _tr("Tür einpassen")
        return _tr("Schubladen einpassen")

    def options_widget(self) -> QWidget:
        self.options = _FrontOptions(self)
        return self.options

    def start(self) -> None:
        self.scene.renderer.AddActor(self.preview)
        self._show(None)

    def finish(self) -> None:
        self.options = None  # the panel deletes the widget
        self.scene.renderer.RemoveActor(self.preview)
        self.scene.viewport.render()

    def settings_changed(self) -> None:
        """Settings changed (keys or panel): new preview and panel values."""
        if self.options is not None:
            self.options.sync()
        self._show(self.plan_at(*self._last) if self._last is not None else None)

    def _thickness(self, material_id: str) -> float:
        doc = self.scene.controller.doc
        material = doc.materials.get(material_id) or builtin_material(material_id)
        return material.thickness

    def plan_at(self, x: int, y: int) -> front.FrontPlan | None:
        scene = self.scene
        hit = scene.pick_face(x, y)
        if hit is None:
            return None
        board_id, point, normal, sign = hit
        controller = scene.controller
        doc, evaluation = controller.doc, controller.evaluation
        comp = fit.compartment(doc, evaluation, point, normal, sign)
        if comp is None:
            return None
        s = self.settings
        door = self.kind == front.FrontKind.DOOR
        op = front.opening(doc, evaluation, comp, point, s.whole and door)
        if op is None:
            return None
        # material and thickness of the thickest carcass part around
        around = op.board_ids or [board_id]
        source = max(around, key=lambda b: evaluation.boards[b].thickness)
        material_id = doc.boards[source].material_id
        thickness = front.Term(
            doc.display_expression(source, "thickness"), evaluation.boards[source].thickness
        )
        if door:
            return front.plan_door(op, s, material_id, thickness)
        return front.plan_drawer(
            op,
            s,
            material_id,
            thickness,
            self._thickness(s.box_material_id),
            self._thickness(s.bottom_material_id),
        )

    def _show(self, plan: front.FrontPlan | None) -> None:
        self.plan = plan
        actor = self.preview
        actor.SetVisibility(plan is not None)
        if plan is not None:
            append = vtkAppendPolyData()
            for lo, size in plan.boxes():
                bounds = (lo[0], lo[0] + size[0], lo[1], lo[1] + size[1], lo[2], lo[2] + size[2])
                append.AddInputData(pv.Box(bounds=bounds))
            append.Update()
            mapper = actor.GetMapper()
            assert isinstance(mapper, vtkPolyDataMapper)
            mapper.SetInputData(append.GetOutput())
        self._status(plan)
        self.scene.viewport.render()

    def describe(self) -> str:
        """Current settings, as shown in the status line."""
        s = self.settings
        overlay = s.mounting == front.Mounting.OVERLAY
        mounting = _tr("aufliegend") if overlay else _tr("einliegend")
        if self.kind == front.FrontKind.DOOR:
            if s.leaves == 2:
                leaves = _tr("zweiflügelig")
            elif s.hinge_right:
                leaves = _tr("einflügelig, Anschlag rechts")
            else:
                leaves = _tr("einflügelig, Anschlag links")
            span = _tr("ganzes Möbel") if s.whole else _tr("Fach")
            what = f"{mounting}, {leaves}, {span}"
        else:
            what = _tr("{0}, {1} übereinander").format(mounting, s.drawers)
        return what + ", " + _tr("Fuge {0} mm").format(number(s.gap))

    def _status(self, plan: front.FrontPlan | None) -> None:
        if self.kind == front.FrontKind.DOOR:
            name = _tr("Tür")
            keys = _tr("L: auf-/einliegend, 1/2: Flügel, A: Anschlag, G: ganzes Möbel/Fach")
        else:
            name = _tr("Schublade")
            keys = _tr("L: auf-/einliegend, 1–9: Anzahl")
        if plan is None:
            text = _tr("{0} einpassen ({1}): in ein Fach zeigen   ({2}, Esc: beenden)")
            self.scene.status_message.emit(text.format(name, self.describe(), keys))
            return
        size = " × ".join(number(v) for v in plan.parts[0].boxes[0][1])
        text = _tr("{0}: {1}, Front {2} mm   (Klick: einfügen, {3}, Esc: beenden)")
        self.scene.status_message.emit(text.format(name, self.describe(), size, keys))

    # ------------------------------------------------------------------ events
    def hover(self, x: int, y: int) -> None:
        self._last = (x, y)
        self._show(self.plan_at(x, y))

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        plan = self.plan_at(x, y)
        if plan is None:
            return False  # empty space: orbit
        self.scene.controller.insert_front(plan)
        self._show(None)
        return True

    def type_key(self, key: int, text: str) -> bool:
        s = self.settings
        door = self.kind == front.FrontKind.DOOR
        if key == Qt.Key.Key_L:
            overlay = s.mounting == front.Mounting.OVERLAY
            s.mounting = front.Mounting.INSET if overlay else front.Mounting.OVERLAY
        elif key == Qt.Key.Key_G and door:
            s.whole = not s.whole
        elif key == Qt.Key.Key_A and door:
            s.hinge_right = not s.hinge_right
        elif text and text in "12" and door:
            s.leaves = int(text)
        elif text and text in "123456789" and not door:
            s.drawers = int(text)
        else:
            return False
        self.settings_changed()
        return True


class _FrontOptions(QWidget):
    """Door / drawer settings in the tool panel."""

    def __init__(self, tool: FrontTool) -> None:
        super().__init__()
        self.tool = tool
        self._syncing = False
        door = tool.kind == front.FrontKind.DOOR
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.mounting = QComboBox()
        self.mounting.addItem(_tr("aufliegend"), front.Mounting.OVERLAY)
        self.mounting.addItem(_tr("einliegend"), front.Mounting.INSET)
        self.mounting.setToolTip(_tr("Taste L"))
        form.addRow(_tr("Montage:"), self.mounting)
        self.gap = self._mm(0.0, 20.0, 0.5)
        form.addRow(_tr("Fuge:"), self.gap)
        self.leaves = QComboBox()
        self.leaves.addItem(_tr("einflügelig"), 1)
        self.leaves.addItem(_tr("zweiflügelig"), 2)
        self.leaves.setToolTip(_tr("Tasten 1 / 2"))
        self.hinge = QComboBox()
        self.hinge.addItem(_tr("links"), False)
        self.hinge.addItem(_tr("rechts"), True)
        self.hinge.setToolTip(_tr("Taste A"))
        self.whole = QCheckBox(_tr("über das ganze Möbel"))
        self.whole.setToolTip(_tr("Taste G: innere Teile übergehen"))
        self.drawers = QSpinBox()
        self.drawers.setRange(1, 9)
        self.drawers.setToolTip(_tr("Tasten 1 bis 9"))
        self.clearance = self._mm(0.0, 50.0, 0.5)
        self.bottom_gap = self._mm(0.0, 200.0, 1.0)
        self.top_gap = self._mm(0.0, 200.0, 1.0)
        self.back_gap = self._mm(0.0, 200.0, 1.0)
        if door:
            form.addRow(_tr("Flügel:"), self.leaves)
            form.addRow(_tr("Anschlag:"), self.hinge)
            form.addRow("", self.whole)
        else:
            form.addRow(_tr("Anzahl:"), self.drawers)
            form.addRow(_tr("Seitenluft (Auszug):"), self.clearance)
            form.addRow(_tr("Luft unten:"), self.bottom_gap)
            form.addRow(_tr("Luft oben:"), self.top_gap)
            form.addRow(_tr("Luft hinten:"), self.back_gap)
        for widget in (self.mounting, self.leaves, self.hinge, self.whole):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys stay in the 3D view
        self.sync()
        self.mounting.currentIndexChanged.connect(self._apply)
        self.leaves.currentIndexChanged.connect(self._apply)
        self.hinge.currentIndexChanged.connect(self._apply)
        self.whole.toggled.connect(self._apply)
        for spin in (
            self.gap,
            self.drawers,
            self.clearance,
            self.bottom_gap,
            self.top_gap,
            self.back_gap,
        ):
            spin.valueChanged.connect(self._apply)

    @staticmethod
    def _mm(low: float, high: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(1)
        spin.setSuffix(" mm")
        return spin

    def sync(self) -> None:
        s = self.tool.settings
        self._syncing = True
        try:
            self.mounting.setCurrentIndex(self.mounting.findData(s.mounting))
            self.gap.setValue(s.gap)
            self.leaves.setCurrentIndex(self.leaves.findData(s.leaves))
            self.hinge.setCurrentIndex(self.hinge.findData(s.hinge_right))
            self.hinge.setEnabled(s.leaves == 1)
            self.whole.setChecked(s.whole)
            self.drawers.setValue(s.drawers)
            self.clearance.setValue(s.slide_clearance)
            self.bottom_gap.setValue(s.bottom_gap)
            self.top_gap.setValue(s.top_gap)
            self.back_gap.setValue(s.back_gap)
        finally:
            self._syncing = False

    def _apply(self) -> None:
        if self._syncing:
            return
        s = self.tool.settings
        s.mounting = self.mounting.currentData()
        s.gap = self.gap.value()
        s.leaves = self.leaves.currentData()
        s.hinge_right = self.hinge.currentData()
        s.whole = self.whole.isChecked()
        s.drawers = self.drawers.value()
        s.slide_clearance = self.clearance.value()
        s.bottom_gap = self.bottom_gap.value()
        s.top_gap = self.top_gap.value()
        s.back_gap = self.back_gap.value()
        self.tool.settings_changed()


DOOR_ANGLE = 100.0  # degrees a door opens in the simulation
SIMULATION_STEPS = 15  # animation frames for opening / closing
SIMULATION_INTERVAL_MS = 20


@dataclass
class Motion:
    """How one door leaf or drawer moves: rotation about a vertical axis
    through ``pivot`` (doors) or a shift by ``travel`` (drawers)."""

    members: list[tuple[str, int]]  # (board, copy) moving together
    pivot: Vec3 | None = None
    angle: float = 0.0  # full opening, degrees about +Z
    travel: Vec3 = (0.0, 0.0, 0.0)
    progress: float = 0.0  # 0 closed .. 1 open
    target: float = 0.0
    actors: list[vtkActor] = field(default_factory=list)

    def transform(self) -> vtkTransform | None:
        if self.progress <= 0:
            return None
        t = self.progress * self.progress * (3 - 2 * self.progress)  # ease in and out
        result = vtkTransform()
        result.PostMultiply()
        if self.pivot is not None:
            px, py, pz = self.pivot
            result.Translate(-px, -py, -pz)
            result.RotateZ(self.angle * t)
            result.Translate(px, py, pz)
        else:
            result.Translate(self.travel[0] * t, self.travel[1] * t, self.travel[2] * t)
        return result


class SimulationTool(Tool):
    """Try doors and drawers: a click opens a door leaf or pulls a drawer out
    on its slides, the next click closes it. Only the view moves."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(scene)
        self.motions: dict[tuple[str, int], Motion] = {}
        self.timer = QTimer()
        self.timer.setInterval(SIMULATION_INTERVAL_MS)
        self.timer.timeout.connect(self.step)

    def title(self) -> str:
        return _tr("Simulation")

    def start(self) -> None:
        self.scene.status_message.emit(
            _tr(
                "Tür oder Schubladenfront anklicken: öffnet sie, noch einmal: "
                "schließt sie   (Esc: beenden)"
            )
        )

    def finish(self) -> None:
        self.timer.stop()
        self.motions.clear()
        self.scene.rebuild()  # everything back in place

    # ---------------------------------------------------------------- motions
    def motion_for(self, board_id: str, copy: int) -> Motion | None:
        """The door leaf or the drawer that ``board_id`` copy ``copy`` belongs to."""
        doc = self.scene.controller.doc
        evaluation = self.scene.controller.evaluation
        solids = self.scene.hardware_solids()
        hinges = [
            f
            for f in doc.fittings.values()
            if f.kind == FittingKind.HINGE and f.part_id == board_id
        ]
        state = evaluation.boards.get(board_id)
        if state is None:
            return None
        origins = state.instance_origins()
        if copy >= len(origins):
            return None
        lo = origins[copy]
        hi = (lo[0] + state.size[0], lo[1] + state.size[1], lo[2] + state.size[2])
        if hinges:
            centre = (lo[0] + hi[0]) / 2
            cups = [
                (x.lo[0] + x.hi[0]) / 2
                for x in solids
                if x.kind == FittingKind.HINGE and x.board_id == board_id and x.copy == copy
            ]
            if cups:
                left = sum(cups) / len(cups) < centre
            else:
                carrier = evaluation.boards.get(hinges[0].carrier_id or "")
                left = carrier is None or carrier.x < centre
            pivot = (lo[0] if left else hi[0], lo[1], lo[2])
            return Motion([(board_id, copy)], pivot, -DOOR_ANGLE if left else DOOR_ANGLE)
        group = doc.boards[board_id].group_id
        if group is None:
            return None
        members = [b.id for b in doc.boards.values() if b.group_id == group]
        slides = [
            f for f in doc.fittings.values() if f.kind == FittingKind.SLIDE and f.part_id in members
        ]
        if not slides:
            return None
        rails = [
            x.hi[1] - x.lo[1]
            for x in solids
            if x.kind == FittingKind.SLIDE and x.board_id not in members
        ]
        depth = max(
            (evaluation.boards[m].size[1] for m in members if m in evaluation.boards),
            default=0.0,
        )
        travel = max(rails) if rails else depth * 2 / 3
        return Motion(
            [(m, copy) for m in members if copy < len(evaluation.boards[m].instance_origins())],
            travel=(0.0, -travel, 0.0),
        )

    def _apply(self, motion: Motion) -> None:
        t = motion.transform()
        for actor in motion.actors:
            actor.SetUserTransform(t)

    def _collect(self, motion: Motion) -> None:
        motion.actors = [
            a
            for board_id, copy in motion.members
            for a in self.scene.instance_actors(board_id, copy)
        ]

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        hit = self.scene.instance_at(x, y)
        motion = None
        if hit is not None:
            motion = next((m for m in self.motions.values() if hit in m.members), None)
            if motion is None:
                motion = self.motion_for(*hit)
                if motion is not None:
                    self._collect(motion)
                    self.motions[motion.members[0]] = motion
        if motion is None:
            self.scene.status_message.emit(
                _tr("Hier ist keine Tür und keine Schublade mit Auszügen   (Esc: beenden)")
            )
            return True
        motion.target = 0.0 if motion.target > 0 else 1.0
        self.timer.start()
        return True

    def step(self) -> None:
        """One animation frame towards every motion's target."""
        delta = 1 / SIMULATION_STEPS
        busy = False
        for motion in self.motions.values():
            if motion.progress == motion.target:
                continue
            if motion.progress < motion.target:
                motion.progress = min(motion.target, motion.progress + delta)
            else:
                motion.progress = max(motion.target, motion.progress - delta)
            busy = busy or motion.progress != motion.target
            self._apply(motion)
        if not busy:
            self.timer.stop()
        self.scene.viewport.render()

    def run_to_end(self) -> None:
        """Finish all running motions at once."""
        while self.timer.isActive():
            self.step()

    def scene_rebuilt(self) -> None:
        boards = self.scene.controller.doc.boards
        for key, motion in list(self.motions.items()):
            if any(b not in boards for b, _copy in motion.members):
                del self.motions[key]
                continue
            self._collect(motion)
            self._apply(motion)


def scene_shift(scene: Scene) -> bool:
    return bool(scene.viewport.iren.interactor.GetShiftKey())


class ShelfPinTool(Tool):
    """Point at the inner face of a side: a row of shelf pin holes (preview);
    a click adds it."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(scene)
        self.target: tuple[str, int] | None = None  # board, face side
        self.holes = 0
        self.preview = _box_actor()

    def title(self) -> str:
        return _tr("Lochreihe")

    def start(self) -> None:
        self.scene.renderer.AddActor(self.preview)
        self._show(None)

    def finish(self) -> None:
        self.scene.renderer.RemoveActor(self.preview)
        self.scene.viewport.render()

    def target_at(self, x: int, y: int) -> tuple[str, int] | None:
        hit = self.scene.pick_face(x, y)
        if hit is None:
            return None
        board_id, _point, axis, sign = hit
        state = self.scene.controller.evaluation.boards.get(board_id)
        if state is None or state.rotated:
            return None
        thickness = state.orientation.thickness_axis.position
        if axis != thickness or axis == 2:
            return None  # only the large faces of upright parts
        return board_id, sign

    def _exists(self, target: tuple[str, int]) -> bool:
        board_id, side = target
        return any(
            f.kind == FittingKind.SHELF_PINS and f.side == side
            for f in self.scene.controller.doc.fittings_of(board_id)
        )

    def _show(self, target: tuple[str, int] | None) -> None:
        self.target = target
        holes = []
        if target is not None:
            fitting = Fitting("", FittingKind.SHELF_PINS, target[0], None, target[1])
            holes = fitting_holes(self.scene.controller.evaluation.boards, fitting)
        self.holes = len(holes)
        self.preview.SetVisibility(bool(holes))
        if holes:
            append = vtkAppendPolyData()
            for _board, hole in holes:
                direction = [0.0, 0.0, 0.0]
                direction[hole.axis] = float(hole.direction)
                centre = list(hole.center)
                centre[hole.axis] += hole.direction * hole.depth / 2
                append.AddInputData(
                    pv.Cylinder(
                        center=centre,
                        direction=direction,
                        radius=hole.diameter / 2,
                        height=hole.depth,
                        resolution=12,
                    )
                )
            append.Update()
            mapper = self.preview.GetMapper()
            assert isinstance(mapper, vtkPolyDataMapper)
            mapper.SetInputData(append.GetOutput())
        if target is None:
            text = _tr("Lochreihe: auf die Innenseite einer Seitenwand zeigen   (Esc: beenden)")
        elif self._exists(target):
            text = _tr("Lochreihe: diese Seite hat schon eine   (Esc: beenden)")
        elif not holes:
            text = _tr("Lochreihe: hier ist kein Platz   (Esc: beenden)")
        else:
            text = _tr("Lochreihe: {0} Bohrungen   (Klick: anlegen, Esc: beenden)").format(
                len(holes)
            )
        self.scene.status_message.emit(text)
        self.scene.viewport.render()

    def hover(self, x: int, y: int) -> None:
        self._show(self.target_at(x, y))

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        target = self.target_at(x, y)
        if target is None:
            return False  # empty space: orbit
        if self.holes and not self._exists(target):
            self.scene.controller.add_fitting(FittingKind.SHELF_PINS, target[0], None, target[1])
        self._show(target)
        return True


class MeasureTool(Tool):
    """Click two points (corners, edge middles, faces) - the distance shows in
    the status bar and as a line."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(scene)
        self.first: Vec3 | None = None
        self.result: tuple[Vec3, Vec3] | None = None
        self.line = vtkActor()
        self.line.SetMapper(vtkPolyDataMapper())
        prop = self.line.GetProperty()
        prop.SetColor(1.0, 0.55, 0.0)
        prop.SetLineWidth(3.0)
        prop.SetLighting(False)
        self.line.PickableOff()
        self.line.VisibilityOff()
        self.marker = vtkActor()
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(pv.Sphere(radius=0.5))
        self.marker.SetMapper(mapper)
        self.marker.GetProperty().SetColor(1.0, 0.55, 0.0)
        self.marker.PickableOff()
        self.marker.VisibilityOff()

    def title(self) -> str:
        return _tr("Messen")

    def start(self) -> None:
        self.scene.overlay.AddActor(self.line)
        self.scene.overlay.AddActor(self.marker)
        self.scene.status_message.emit(_tr("Messen: ersten Punkt klicken   (Esc: beenden)"))

    def finish(self) -> None:
        self.scene.overlay.RemoveActor(self.line)
        self.scene.overlay.RemoveActor(self.marker)
        self.scene.viewport.render()

    def snap_point(self, x: int, y: int) -> Vec3 | None:
        """Nearest box corner or edge middle on screen, else the surface point."""
        scene = self.scene
        board_id, point = scene.pick(x, y)
        if point is None or board_id is None:
            return None
        best: tuple[float, Vec3] | None = None
        controller = scene.controller
        for box in fit.boxes(controller.doc, controller.evaluation):
            if box.board_id != board_id:
                continue
            for candidate in _snap_candidates(box.lo, box.hi):
                sx, sy = scene.to_display(candidate)
                distance = math.hypot(sx - x, sy - y)
                if distance <= MEASURE_SNAP_PX and (best is None or distance < best[0]):
                    best = (distance, candidate)
        return best[1] if best is not None else point

    def _marker_at(self, point: Vec3 | None) -> None:
        self.marker.SetVisibility(point is not None)
        if point is not None:
            size = 8 * self.scene.world_per_pixel(point)
            self.marker.SetPosition(*point)
            self.marker.SetScale(size, size, size)

    def _line(self, a: Vec3, b: Vec3) -> None:
        mapper = self.line.GetMapper()
        assert isinstance(mapper, vtkPolyDataMapper)
        mapper.SetInputData(pv.Line(a, b))
        self.line.VisibilityOn()

    def hover(self, x: int, y: int) -> None:
        point = self.snap_point(x, y)
        self._marker_at(point)
        if self.first is not None and point is not None:
            self._line(self.first, point)
            self._report(self.first, point, final=False)
        self.scene.viewport.render()

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        point = self.snap_point(x, y)
        if point is None:
            return False
        if self.first is None:
            self.first = point
            self.result = None
            self.line.VisibilityOff()
            self.scene.status_message.emit(_tr("Messen: zweiten Punkt klicken"))
        else:
            self.result = (self.first, point)
            self._line(self.first, point)
            self._report(self.first, point, final=True)
            self.first = None
        self.scene.viewport.render()
        return True

    def _report(self, a: Vec3, b: Vec3, final: bool) -> None:
        d = [b[i] - a[i] for i in range(3)]
        distance = math.sqrt(sum(v * v for v in d))
        text = _tr("Abstand {0} mm   (ΔX {1}, ΔY {2}, ΔZ {3})").format(
            number(distance), number(abs(d[0])), number(abs(d[1])), number(abs(d[2]))
        )
        if final:
            text += _tr("   — nächster Punkt: neue Messung, Esc: beenden")
        self.scene.status_message.emit(text)


def _snap_candidates(lo: Vec3, hi: Vec3) -> list[Vec3]:
    """Corners and edge middles of a box."""
    values = [(lo[i], (lo[i] + hi[i]) / 2, hi[i]) for i in range(3)]
    points: list[Vec3] = []
    for ix in range(3):
        for iy in range(3):
            for iz in range(3):
                middles = (ix == 1) + (iy == 1) + (iz == 1)
                if middles <= 1:  # corner (0) or edge middle (1)
                    points.append((values[0][ix], values[1][iy], values[2][iz]))
    return points


# ====================================================================== faces
FACE_COLOR = (1.0, 0.55, 0.0)
FACE_TOLERANCE = 1.0  # mm: hit point to box face


@dataclass(frozen=True, slots=True)
class PickedFace:
    """A flat face under the cursor, normal along a world axis."""

    board_id: str
    axis: int
    sign: int  # direction of the outward normal
    value: float  # position of the face plane along ``axis``
    point: Vec3
    rect: tuple[Vec3, Vec3] | None  # face corners (None: not a box face)


def pick_face(scene: Scene, x: int, y: int) -> PickedFace | None:
    """The face under the cursor, snapped to the exact face of the part's box."""
    hit = scene.pick_face(x, y)
    if hit is None:
        return None
    board_id, point, axis, sign = hit
    controller = scene.controller
    for box in fit.boxes(controller.doc, controller.evaluation):
        if box.board_id != board_id:
            continue
        plane = box.hi[axis] if sign > 0 else box.lo[axis]
        if abs(plane - point[axis]) > FACE_TOLERANCE:
            continue
        if any(
            not box.lo[k] - FACE_TOLERANCE <= point[k] <= box.hi[k] + FACE_TOLERANCE
            for k in range(3)
            if k != axis
        ):
            continue
        lo, hi = list(box.lo), list(box.hi)
        lo[axis] = hi[axis] = plane
        return PickedFace(
            board_id, axis, sign, plane, point, ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))
        )
    return PickedFace(board_id, axis, sign, point[axis], point, None)


def distance_shift(reference: PickedFace, target: PickedFace, distance: float) -> float:
    """How far the target's part moves along the axis so that its face is
    ``distance`` from the reference face (on the side where it is now; faces
    that touch move apart in the direction the target face looks away from)."""
    gap = target.value - reference.value
    touching = abs(gap) <= 1e-6
    direction = -float(target.sign) if touching else (1.0 if gap > 0 else -1.0)
    return reference.value + direction * distance - target.value


def _face_actor() -> vtkActor:
    actor = _box_actor()
    prop = actor.GetProperty()
    prop.SetColor(*FACE_COLOR)
    prop.SetOpacity(0.4)
    prop.SetLighting(False)
    return actor


class FaceMeasureTool(Tool):
    """Click two parallel faces - their distance shows in the status bar and
    as a line square to the first face."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(scene)
        self.first: PickedFace | None = None
        self.result: float | None = None
        self.hover_face = _face_actor()
        self.first_face = _face_actor()
        self.line = vtkActor()
        self.line.SetMapper(vtkPolyDataMapper())
        prop = self.line.GetProperty()
        prop.SetColor(*FACE_COLOR)
        prop.SetLineWidth(3.0)
        prop.SetLighting(False)
        self.line.PickableOff()
        self.line.VisibilityOff()

    def title(self) -> str:
        return _tr("Flächen messen")

    # ------------------------------------------------------------------ texts
    def start_hint(self) -> str:
        return _tr("Flächen messen: erste Fläche klicken   (Esc: beenden)")

    def second_hint(self) -> str:
        return _tr("Zweite, parallele Fläche klicken   (Esc: neu beginnen)")

    # ------------------------------------------------------------------ actors
    def start(self) -> None:
        for actor in (self.hover_face, self.first_face, self.line):
            self.scene.overlay.AddActor(actor)
        self.scene.status_message.emit(self.start_hint())

    def finish(self) -> None:
        for actor in (self.hover_face, self.first_face, self.line):
            self.scene.overlay.RemoveActor(actor)
        self.scene.viewport.render()

    def _show_face(self, actor: vtkActor, face: PickedFace | None) -> None:
        if face is None or face.rect is None:
            actor.VisibilityOff()
            return
        lo, hi = face.rect
        pos = list(lo)
        pos[face.axis] += 0.2 * face.sign  # a hair in front of the face
        actor.SetPosition(*pos)
        actor.SetScale(*(max(hi[k] - lo[k], 0.01) for k in range(3)))
        actor.VisibilityOn()

    def _line_between(self, reference: PickedFace, face: PickedFace) -> None:
        start = list(face.point)
        start[face.axis] = face.value
        end = list(start)
        end[reference.axis] = reference.value
        mapper = self.line.GetMapper()
        assert isinstance(mapper, vtkPolyDataMapper)
        mapper.SetInputData(pv.Line(start, end))
        self.line.VisibilityOn()

    def reset(self) -> None:
        self.first = None
        self.first_face.VisibilityOff()
        self.line.VisibilityOff()
        self.scene.status_message.emit(self.start_hint())
        self.scene.viewport.render()

    # ------------------------------------------------------------------ events
    def hover(self, x: int, y: int) -> None:
        face = pick_face(self.scene, x, y)
        self._show_face(self.hover_face, face)
        first = self.first
        if first is not None and face is not None:
            if face.axis == first.axis:
                self._line_between(first, face)
                self.report(abs(face.value - first.value), final=False)
            else:
                self.line.VisibilityOff()
                self.scene.status_message.emit(_tr("Diese Fläche ist nicht parallel zur ersten"))
        self.scene.viewport.render()

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        face = pick_face(self.scene, x, y)
        if face is None:
            return False  # empty space: orbit
        first = self.first
        if first is None:
            self.first = face
            self.result = None
            self._show_face(self.first_face, face)
            self.line.VisibilityOff()
            self.scene.status_message.emit(self.second_hint())
        elif face.axis != first.axis:
            self.scene.status_message.emit(
                _tr("Diese Fläche ist nicht parallel zur ersten – bitte eine parallele wählen")
            )
        else:
            self._line_between(first, face)
            self.complete(first, face)
        self.scene.viewport.render()
        return True

    def complete(self, first: PickedFace, second: PickedFace) -> None:
        self.result = abs(second.value - first.value)
        self.report(self.result, final=True)
        self.first = None
        self.first_face.VisibilityOff()

    def report(self, distance: float, final: bool) -> None:
        text = _tr("Abstand der Flächen {0} mm").format(number(distance))
        if final:
            text += _tr("   — nächste Fläche: neue Messung, Esc: beenden")
        self.scene.status_message.emit(text)

    def cancel(self) -> None:
        if self.first is not None:
            self.reset()
        else:
            self.scene.end_tool()


class SetDistanceTool(FaceMeasureTool):
    """Click a reference face, then a parallel face of another part, type the
    new distance: that part moves (once - nothing is linked)."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(scene)
        self.target: tuple[PickedFace, PickedFace] | None = None
        self.typed = ""

    def title(self) -> str:
        return _tr("Abstand setzen")

    def start_hint(self) -> str:
        return _tr("Abstand setzen: Bezugsfläche klicken (bleibt stehen)   (Esc: beenden)")

    def second_hint(self) -> str:
        return _tr("Parallele Fläche des Teils klicken, das verschoben wird   (Esc: neu beginnen)")

    def reset(self) -> None:
        self.target = None
        self.typed = ""
        super().reset()

    def hover(self, x: int, y: int) -> None:
        if self.target is None:
            super().hover(x, y)
        else:  # waiting for the number: only show the face under the cursor
            self._show_face(self.hover_face, pick_face(self.scene, x, y))
            self.scene.viewport.render()

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        if self.target is not None:  # a click instead of a number: start over
            self.target = None
            self.typed = ""
            self.first = None
            self.first_face.VisibilityOff()
        return super().press(x, y, ctrl)

    def complete(self, first: PickedFace, second: PickedFace) -> None:
        if second.board_id == first.board_id:
            self.scene.status_message.emit(
                _tr("Bitte eine Fläche eines anderen Teils wählen (das verschoben wird)")
            )
            return
        self.target = (first, second)
        self.typed = ""
        self._prompt()

    def _prompt(self) -> None:
        assert self.target is not None
        first, second = self.target
        name = self.scene.controller.doc.boards[second.board_id].name
        self.scene.status_message.emit(
            _tr(
                "Abstand jetzt {0} mm – neuen Abstand tippen: {1}_   "
                "(Enter: „{2}“ verschieben, Esc: abbrechen)"
            ).format(number(abs(second.value - first.value)), self.typed, name)
        )

    def type_key(self, key: int, text: str) -> bool:
        if self.target is None:
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.apply()
            return True
        if key == Qt.Key.Key_Backspace:
            self.typed = self.typed[:-1]
        elif text and text in "0123456789.,":
            self.typed += "." if text == "," else text
        else:
            return False
        self._prompt()
        return True

    def apply(self) -> bool:
        """Move the part to the typed distance; False if nothing valid was typed."""
        if self.target is None:
            return False
        try:
            distance = float(self.typed)
        except ValueError:
            self.scene.status_message.emit(_tr("Bitte zuerst den neuen Abstand tippen"))
            return False
        first, second = self.target
        shift = distance_shift(first, second, distance)
        delta = [0.0, 0.0, 0.0]
        delta[first.axis] = shift
        controller = self.scene.controller
        name = controller.doc.boards[second.board_id].name
        controller.translate([second.board_id], (delta[0], delta[1], delta[2]))
        self.target = None
        self.typed = ""
        self.first = None
        self.first_face.VisibilityOff()
        self.line.VisibilityOff()
        self.scene.status_message.emit(
            _tr("„{0}“ um {1} mm verschoben – Abstand jetzt {2} mm").format(
                name, number(shift), number(distance)
            )
        )
        self.scene.viewport.render()
        return True

    def cancel(self) -> None:
        if self.target is not None:
            self.reset()
        else:
            super().cancel()


_AXES = (Axis.X, Axis.Y, Axis.Z)


class ExtendTool(FaceMeasureTool):
    """Bis Fläche ziehen: click the face of a part that should move, then a
    parallel face of another part - the face goes there and stays linked."""

    def title(self) -> str:
        return _tr("Bis Fläche ziehen")

    def start_hint(self) -> str:
        return _tr("Bis Fläche ziehen: Fläche des Teils klicken, die sich bewegt   (Esc: beenden)")

    def second_hint(self) -> str:
        return _tr("Zielfläche eines anderen Teils klicken   (Esc: neu beginnen)")

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        if self.first is None:
            face = pick_face(self.scene, x, y)
            if face is None:
                return False
            controller = self.scene.controller
            if face.rect is None or not transform.resizable(
                controller.doc, controller.evaluation, face.board_id, _AXES[face.axis]
            ):
                self.scene.status_message.emit(
                    _tr("Diese Fläche kann nicht gezogen werden (gedrehtes oder Skizzenteil)")
                )
                return True
        return super().press(x, y, ctrl)

    def report(self, distance: float, final: bool) -> None:
        if not final:
            self.scene.status_message.emit(_tr("Weg {0} mm").format(number(distance)))

    def complete(self, first: PickedFace, second: PickedFace) -> None:
        if second.board_id == first.board_id:
            self.scene.status_message.emit(_tr("Bitte eine Fläche eines anderen Teils wählen"))
            return
        controller = self.scene.controller
        doc = controller.doc
        name, target = doc.boards[first.board_id].name, doc.boards[second.board_id].name
        formula = controller.extend_board(
            first.board_id, _AXES[first.axis], first.sign, second.board_id, second.value
        )
        self.first = None
        self.first_face.VisibilityOff()
        self.line.VisibilityOff()
        if formula is None:
            self.scene.status_message.emit(
                _tr("„{0}“ kann nicht bis dorthin gezogen werden (Größe wäre 0)").format(name)
            )
        elif formula:
            self.scene.status_message.emit(
                _tr("„{0}“ bis „{1}“ gezogen und verknüpft ({2})").format(name, target, formula)
            )
        else:
            self.scene.status_message.emit(
                _tr("„{0}“ bis „{1}“ gezogen (ohne Verknüpfung: „{1}“ hängt von „{0}“ ab)").format(
                    name, target
                )
            )
