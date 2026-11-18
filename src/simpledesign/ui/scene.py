"""Boards in the 3D view, click selection and the move gizmo.

* Every pattern copy of a board is one ``vtkActor`` sharing a box mesh that is
  cached by size; the actor is placed with ``SetPosition`` (no re-meshing).
* The gizmo (three arrows) lives in an overlay renderer (layer 1) that shares
  the camera, so it is always drawn on top. Its size is kept constant on screen.
* Dragging an arrow moves the board along that axis. Near a face of another
  board it snaps and writes a formula (``Seite.x2``) instead of a number.
"""

from __future__ import annotations

import math

import numpy as np
import pyvista as pv
from PySide6.QtCore import QObject, Signal
from vtkmodules.vtkCommonCore import vtkCommand
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkCellPicker,
    vtkPolyDataMapper,
    vtkRenderer,
)

from simpledesign.app.controller import Controller
from simpledesign.app.snapping import SnapTarget, Vec3, axis_parameter, best_snap, snap_targets
from simpledesign.core.geometry.mesh import part_mesh
from simpledesign.core.geometry.placement import world_bounds
from simpledesign.core.model.machining import PartShape
from simpledesign.core.model.part import Axis
from simpledesign.core.params.expression import add_offset
from simpledesign.ui.texts import number
from simpledesign.ui.viewport import Viewport

CLICK_TOLERANCE_PX = 3
SNAP_TOLERANCE_PX = 8
GIZMO_SIZE_PX = 90
SELECTION_COLOR = "#2f7de1"
EDGE_COLOR = "#4b4136"
TRANSPARENT_OPACITY = 0.3
AXIS_COLORS = {Axis.X: "#d64545", Axis.Y: "#3fa34d", Axis.Z: "#3b6fd6"}
HIGHLIGHT_COLOR = "#ffcc33"
UNIT = {Axis.X: (1.0, 0.0, 0.0), Axis.Y: (0.0, 1.0, 0.0), Axis.Z: (0.0, 0.0, 1.0)}

type Size = tuple[float, float, float]


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return (int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255)


def _edges(size: Size) -> pv.PolyData:
    sx, sy, sz = size
    corners = np.array(
        [[x, y, z] for z in (0, sz) for y in (0, sy) for x in (0, sx)], dtype=np.float64
    )
    pairs = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7)]
    pairs += [(0, 4), (1, 5), (2, 6), (3, 7)]
    lines = np.array([v for a, b in pairs for v in (2, a, b)], dtype=np.int64)
    return pv.PolyData(corners, lines=lines)


class _Drag:
    def __init__(self, board_id: str, axis: Axis, anchor: Vec3, start: float, t0: float) -> None:
        self.board_id = board_id
        self.axis = axis
        self.anchor = anchor  # gizmo origin at drag start
        self.start = start  # board start coordinate along the axis at drag start
        self.t0 = t0  # axis parameter under the cursor at drag start
        self.delta = 0.0
        self.snap: SnapTarget | None = None
        self.targets: list[SnapTarget] = []


class Scene(QObject):
    status_message = Signal(str)

    def __init__(self, viewport: Viewport, controller: Controller) -> None:
        super().__init__(viewport)
        self.viewport = viewport
        self.controller = controller
        self.renderer: vtkRenderer = viewport.renderer
        self.snapping_enabled = True
        self.snap_tolerance_px = SNAP_TOLERANCE_PX

        self._board_actors: dict[str, list[tuple[vtkActor, vtkActor]]] = {}
        self._actor_board: dict[int, str] = {}  # id(actor) -> board id
        # Actor pairs are reused across rebuilds: adding/removing VTK actors
        # costs a context switch each, which dominates large documents.
        self._pool: list[tuple[vtkActor, vtkActor]] = []
        self._meshes: dict[object, tuple[vtkPolyDataMapper, vtkPolyDataMapper]] = {}
        # Software picking: works in every OpenGL context (no hardware selector).
        self._picker = vtkCellPicker()
        self._picker.SetTolerance(0.002)
        self._press: tuple[int, int] | None = None
        self._drag: _Drag | None = None

        self._create_overlay()
        viewport.interaction_style.pointer_handler = self
        controller.document_changed.connect(self.rebuild)
        controller.selection_changed.connect(self.rebuild)
        self.rebuild()

    # ================================================================== boards
    def _mappers(self, shape: PartShape) -> tuple[vtkPolyDataMapper, vtkPolyDataMapper]:
        size = (round(shape.size[0], 3), round(shape.size[1], 3), round(shape.size[2], 3))
        key = (size, shape.features)
        if key not in self._meshes:
            mesh = part_mesh(size, shape.features)
            faces = pv.PolyData(mesh.points, mesh.faces)
            face_mapper = vtkPolyDataMapper()
            face_mapper.SetInputData(faces)
            edge_mapper = vtkPolyDataMapper()
            if shape.features:
                # Sharp edges of the machined body (box edges, hole rims, grooves).
                edges = faces.clean().extract_feature_edges(
                    feature_angle=30, boundary_edges=False, manifold_edges=False
                )
                edge_mapper.SetInputData(edges)
            else:
                edge_mapper.SetInputData(_edges(size))
            self._meshes[key] = (face_mapper, edge_mapper)
        return self._meshes[key]

    def rebuild(self) -> None:
        self._board_actors = {}
        used = 0
        self._actor_board = {}
        if len(self._meshes) > 256:
            self._meshes.clear()

        doc = self.controller.doc
        evaluation = self.controller.evaluation
        selected = set(self.controller.selection)
        # A selected group selects everything inside it.
        selected |= {
            b.id
            for b in doc.boards.values()
            if any(g in selected for g in doc.scope_chain(b.group_id))
        }
        for board in doc.boards.values():
            if not doc.is_visible(board):
                continue
            is_selected = board.id in selected
            color = SELECTION_COLOR if is_selected else doc.materials[board.material_id].color
            pairs = []
            for shape in evaluation.machining.parts[board.id]:
                face_mapper, edge_mapper = self._mappers(shape)
                origin = shape.origin
                face, edge = self._pooled(used)
                used += 1
                face.SetMapper(face_mapper)
                edge.SetMapper(edge_mapper)
                for actor in (face, edge):
                    actor.SetOrientation(0.0, 0.0, 0.0)
                    actor.SetPosition(*origin)
                    actor.VisibilityOn()
                prop = face.GetProperty()
                prop.SetColor(*_rgb(color))
                prop.SetOpacity(TRANSPARENT_OPACITY if board.transparent else 1.0)
                if shape.rotation is not None:
                    axis, degrees = shape.rotation
                    for actor in (face, edge):
                        actor.RotateWXYZ(degrees, *UNIT[Axis("xyz"[axis])])
                edge_prop = edge.GetProperty()
                edge_prop.SetColor(*_rgb(SELECTION_COLOR if is_selected else EDGE_COLOR))
                edge_prop.SetLineWidth(2.5 if is_selected else 1.0)
                self._actor_board[id(face)] = board.id
                pairs.append((face, edge))
            self._board_actors[board.id] = pairs
        for face, edge in self._pool[used:]:
            face.VisibilityOff()
            edge.VisibilityOff()
        if len(self._pool) > 2 * used + 64:  # shrink after deleting many parts
            for face, edge in self._pool[used:]:
                self.renderer.RemoveActor(face)
                self.renderer.RemoveActor(edge)
            del self._pool[used:]
        self._place_gizmo()
        self.viewport.render()

    def _pooled(self, index: int) -> tuple[vtkActor, vtkActor]:
        if index == len(self._pool):
            face, edge = vtkActor(), vtkActor()
            prop = face.GetProperty()
            prop.SetAmbient(0.25)
            prop.SetDiffuse(0.8)
            edge.PickableOff()
            edge.GetProperty().SetLighting(False)
            self.renderer.AddActor(face)
            self.renderer.AddActor(edge)
            self._pool.append((face, edge))
        return self._pool[index]

    def board_at(self, x: int, y: int) -> str | None:
        if not self._picker.Pick(x, y, 0, self.renderer):
            return None
        actor = self._picker.GetActor()
        return self._actor_board.get(id(actor)) if actor is not None else None

    # ================================================================== gizmo
    def _create_overlay(self) -> None:
        self.overlay = vtkRenderer()
        self.overlay.SetLayer(1)
        self.overlay.SetActiveCamera(self.renderer.GetActiveCamera())
        self.overlay.InteractiveOff()
        render_window = self.viewport.ren_win
        assert render_window is not None
        render_window.SetNumberOfLayers(2)
        render_window.AddRenderer(self.overlay)

        self._arrows: dict[Axis, vtkActor] = {}
        self._arrow_axis: dict[int, Axis] = {}
        for axis, direction in UNIT.items():
            arrow = pv.Arrow(
                start=(0.0, 0.0, 0.0),
                direction=direction,
                tip_length=0.25,
                tip_radius=0.07,
                shaft_radius=0.025,
            )
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(arrow)
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*_rgb(AXIS_COLORS[axis]))
            actor.GetProperty().SetAmbient(0.4)
            actor.VisibilityOff()
            self.overlay.AddActor(actor)
            self._arrows[axis] = actor
            self._arrow_axis[id(actor)] = axis
        self._gizmo_origin: Vec3 | None = None
        self.renderer.AddObserver(vtkCommand.StartEvent, self._scale_gizmo)

    def gizmo_board(self) -> str | None:
        selection = self.controller.selection
        if len(selection) == 1 and selection[0] in self._board_actors:
            return selection[0]
        return None

    def _place_gizmo(self) -> None:
        board_id = self.gizmo_board()
        if board_id is None:
            self._gizmo_origin = None
        else:
            state = self.controller.evaluation.boards[board_id]
            rotation = ("xyz".index(state.rotation_axis.value), state.angle)
            lo, hi = world_bounds(
                (state.x, state.y, state.z), state.size, rotation if state.rotated else None
            )
            self._gizmo_origin = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        self._show_gizmo_at(self._gizmo_origin)

    def _show_gizmo_at(self, origin: Vec3 | None) -> None:
        for actor in self._arrows.values():
            actor.SetVisibility(origin is not None)
            if origin is not None:
                actor.SetPosition(*origin)

    def world_per_pixel(self, point: Vec3) -> float:
        camera = self.renderer.GetActiveCamera()
        height = max(self.renderer.GetSize()[1], 1)
        if camera.GetParallelProjection():
            return 2.0 * camera.GetParallelScale() / height
        position = camera.GetPosition()
        direction = camera.GetDirectionOfProjection()
        depth = sum((point[i] - position[i]) * direction[i] for i in range(3))
        return 2.0 * max(depth, 1e-6) * math.tan(math.radians(camera.GetViewAngle() / 2)) / height

    def _scale_gizmo(self, *_args: object) -> None:
        origin = self._current_gizmo_origin()
        if origin is None:
            return
        scale = GIZMO_SIZE_PX * self.world_per_pixel(origin)
        for actor in self._arrows.values():
            actor.SetScale(scale, scale, scale)

    def _current_gizmo_origin(self) -> Vec3 | None:
        if self._drag is None:
            return self._gizmo_origin
        anchor = list(self._drag.anchor)
        anchor[self._drag.axis.position] += self._drag.delta
        return (anchor[0], anchor[1], anchor[2])

    def _arrow_at(self, x: int, y: int) -> Axis | None:
        if self._gizmo_origin is None or not self._picker.Pick(x, y, 0, self.overlay):
            return None
        actor = self._picker.GetActor()
        return self._arrow_axis.get(id(actor)) if actor is not None else None

    def _ray(self, x: int, y: int) -> tuple[Vec3, Vec3]:
        points = []
        for depth in (0.0, 1.0):
            self.renderer.SetDisplayPoint(x, y, depth)
            self.renderer.DisplayToWorld()
            wx, wy, wz, w = self.renderer.GetWorldPoint()
            points.append((wx / w, wy / w, wz / w))
        near, far = points
        d = (far[0] - near[0], far[1] - near[1], far[2] - near[2])
        length = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) or 1.0
        return near, (d[0] / length, d[1] / length, d[2] / length)

    def _axis_t(self, drag_anchor: Vec3, axis: Axis, x: int, y: int) -> float:
        ray_start, ray_direction = self._ray(x, y)
        return axis_parameter(drag_anchor, UNIT[axis], ray_start, ray_direction)

    # ================================================================== pointer events
    def press(self, x: int, y: int, ctrl: bool) -> None:
        self._press = (x, y)
        axis = self._arrow_at(x, y)
        board_id = self.gizmo_board()
        if axis is None or board_id is None or self._gizmo_origin is None:
            return
        doc = self.controller.doc
        evaluation = self.controller.evaluation
        drag = _Drag(
            board_id,
            axis,
            self._gizmo_origin,
            evaluation.boards[board_id].origin[axis.position],
            self._axis_t(self._gizmo_origin, axis, x, y),
        )
        drag.targets = snap_targets(doc, evaluation, board_id, axis)
        self._drag = drag
        self._arrows[axis].GetProperty().SetColor(*_rgb(HIGHLIGHT_COLOR))

    def move(self, x: int, y: int) -> bool:
        drag = self._drag
        if drag is None:
            return False
        raw_delta = self._axis_t(drag.anchor, drag.axis, x, y) - drag.t0
        raw_start = drag.start + raw_delta
        drag.snap = None
        if self.snapping_enabled and not self.viewport.iren.interactor.GetShiftKey():
            tolerance = self.snap_tolerance_px * self.world_per_pixel(drag.anchor)
            drag.snap = best_snap(drag.targets, raw_start, tolerance)
        if drag.snap is not None:
            drag.delta = drag.snap.value - drag.start
            self.status_message.emit(self.tr("Einrasten an: {f}").format(f=drag.snap.formula))
        else:
            drag.delta = float(round(raw_delta))
            self.status_message.emit(
                self.tr("Verschieben {axis}: {d} mm").format(
                    axis=drag.axis.value.upper(), d=number(drag.delta)
                )
            )
        self._preview(drag)
        return True

    def _preview(self, drag: _Drag) -> None:
        shapes = self.controller.evaluation.machining.parts[drag.board_id]
        for (face, edge), shape in zip(
            self._board_actors.get(drag.board_id, []), shapes, strict=False
        ):
            moved = list(shape.origin)
            moved[drag.axis.position] += drag.delta
            face.SetPosition(*moved)
            edge.SetPosition(*moved)
        self._show_gizmo_at(self._current_gizmo_origin())
        self.viewport.render()

    def release(self, x: int, y: int, ctrl: bool) -> None:
        press, self._press = self._press, None
        drag, self._drag = self._drag, None
        if drag is not None:
            self._arrows[drag.axis].GetProperty().SetColor(*_rgb(AXIS_COLORS[drag.axis]))
            self.status_message.emit("")
            self._commit(drag)
            return
        if press is None or max(abs(x - press[0]), abs(y - press[1])) > CLICK_TOLERANCE_PX:
            return
        board_id = self.board_at(x, y)
        selection = list(self.controller.selection)
        if ctrl:
            if board_id is not None:
                if board_id in selection:
                    selection.remove(board_id)
                else:
                    selection.append(board_id)
            self.controller.set_selection(selection)
        else:
            self.controller.set_selection([board_id] if board_id else [])

    def _commit(self, drag: _Drag) -> None:
        prop = drag.axis.value
        value = drag.start + drag.delta
        if drag.snap is not None:
            formula = drag.snap.formula
        elif abs(drag.delta) >= 0.5:
            current = self.controller.doc.display_expression(drag.board_id, prop)
            formula = add_offset(current, drag.delta)
        else:
            self.rebuild()  # undo the preview
            return
        before = self.controller.doc.stored_expression(drag.board_id, prop)
        self.controller.move_board(drag.board_id, drag.axis, formula, value)
        if self.controller.doc.stored_expression(drag.board_id, prop) == before:
            self.rebuild()
