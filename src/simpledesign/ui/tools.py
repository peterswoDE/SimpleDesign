"""Click tools of the 3D view: fit a new board into a compartment, measure
points or faces, set the distance between two faces.

A tool takes over the pointer events of the view while it is active (the
scene hands them back when the tool ends: Esc, right click).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyvista as pv
from PySide6.QtCore import QCoreApplication, Qt
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from simpledesign.app.snapping import Vec3
from simpledesign.core.materials.library import builtin_material
from simpledesign.core.model import fit
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


def scene_shift(scene: Scene) -> bool:
    return bool(scene.viewport.iren.interactor.GetShiftKey())


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
