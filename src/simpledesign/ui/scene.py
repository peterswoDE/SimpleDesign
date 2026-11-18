"""Boards in the 3D view, selection, moving parts and the move gizmo.

* Every pattern copy of a board is one ``vtkActor`` sharing a box mesh that is
  cached by size; the actor is placed with ``SetPosition`` (no re-meshing).
* The gizmo (three arrows) lives in an overlay renderer (layer 1) that shares
  the camera, so it is always drawn on top. Its size is kept constant on screen
  and its arrows are picked in screen space (generous, independent of zoom).
* Dragging an arrow moves the selection along that axis. A single board snaps
  to faces of other boards and gets a formula (``Seite.x2``) instead of a number.
* Dragging a part itself moves it in the floor plane (or, looking from the
  side, in the vertical plane facing the camera).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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
from simpledesign.ui.viewport import Viewport, world_per_pixel

CLICK_TOLERANCE_PX = 3
SNAP_TOLERANCE_PX = 8
GIZMO_SIZE_PX = 90
GIZMO_PICK_PX = 10  # distance from an arrow (on screen) that still grabs it
SELECTION_COLOR = "#2f7de1"
EDGE_COLOR = "#4b4136"
TRANSPARENT_OPACITY = 0.3
AXIS_COLORS = {Axis.X: "#d64545", Axis.Y: "#3fa34d", Axis.Z: "#3b6fd6"}
HIGHLIGHT_COLOR = "#ffcc33"
UNIT = {Axis.X: (1.0, 0.0, 0.0), Axis.Y: (0.0, 1.0, 0.0), Axis.Z: (0.0, 0.0, 1.0)}
AXES = (Axis.X, Axis.Y, Axis.Z)
# Looking down at least this steeply (|view direction · Z|) drags parts on the floor plane.
FLOOR_DRAG_MIN_Z = 0.35

type Size = tuple[float, float, float]
type Point2 = tuple[float, float]


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


def segment_distance(p: Point2, a: Point2, b: Point2) -> float:
    """Distance of point ``p`` to the segment ``a``-``b`` (2D)."""
    ab = (b[0] - a[0], b[1] - a[1])
    length2 = ab[0] ** 2 + ab[1] ** 2
    t = 0.0 if length2 < 1e-12 else ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - a[0] - t * ab[0], p[1] - a[1] - t * ab[1])


@dataclass(eq=False)
class _Drag:
    """A running move of ``ids`` (boards and groups) by ``delta``."""

    ids: list[str]
    boards: set[str]  # all boards that move (incl. group contents)
    single: str | None  # the board id when exactly one board moves (enables snapping)
    anchor: Vec3  # gizmo origin / grab point at drag start
    delta: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    snaps: dict[Axis, SnapTarget] = field(default_factory=dict)
    targets: dict[Axis, list[SnapTarget]] = field(default_factory=dict)
    # axis drag (gizmo arrow); None = plane drag of a part
    axis: Axis | None = None
    t0: float = 0.0
    normal: int = 2  # plane drag: index of the plane normal's world axis


class Scene(QObject):
    status_message = Signal(str)
    # Right click at a viewport position (logical pixels from the top-left).
    context_menu_requested = Signal(int, int)

    def __init__(self, viewport: Viewport, controller: Controller) -> None:
        super().__init__(viewport)
        self.viewport = viewport
        self.controller = controller
        self.renderer: vtkRenderer = viewport.renderer
        self.snapping_enabled = True
        self.snap_tolerance_px = SNAP_TOLERANCE_PX

        self._board_actors: dict[str, list[tuple[vtkActor, vtkActor]]] = {}
        self._actor_board: dict[int, str] = {}  # id(actor) -> board id
        self._selected_boards: set[str] = set()
        # Actor pairs are reused across rebuilds: adding/removing VTK actors
        # costs a context switch each, which dominates large documents.
        self._pool: list[tuple[vtkActor, vtkActor]] = []
        self._meshes: dict[object, tuple[vtkPolyDataMapper, vtkPolyDataMapper]] = {}
        # Software picking: works in every OpenGL context (no hardware selector).
        self._picker = vtkCellPicker()
        self._picker.SetTolerance(0.0005)
        self._press: tuple[int, int] | None = None
        self._press_board: str | None = None
        self._press_point: Vec3 | None = None
        self._drag: _Drag | None = None
        self._hover_axis: Axis | None = None

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

    def selected_boards(self) -> set[str]:
        """Selected boards, including everything inside selected groups."""
        doc = self.controller.doc
        selected = set(self.controller.selection)
        return {
            b.id
            for b in doc.boards.values()
            if b.id in selected or any(g in selected for g in doc.scope_chain(b.group_id))
        }

    def rebuild(self) -> None:
        self._board_actors = {}
        used = 0
        self._actor_board = {}
        if len(self._meshes) > 256:
            self._meshes.clear()

        doc = self.controller.doc
        evaluation = self.controller.evaluation
        selected = self.selected_boards()
        self._selected_boards = selected
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
                        actor.RotateWXYZ(degrees, *UNIT[AXES[axis]])
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
        return self.pick(x, y)[0]

    def pick(self, x: int, y: int) -> tuple[str | None, Vec3 | None]:
        """The board under a display position and the hit point on its surface."""
        if not self._picker.Pick(x, y, 0, self.renderer):
            return None, None
        actor = self._picker.GetActor()
        board_id = self._actor_board.get(id(actor)) if actor is not None else None
        if board_id is None:
            return None, None
        px, py, pz = self._picker.GetPickPosition()
        return board_id, (px, py, pz)

    def bounds(self, board_ids: set[str]) -> tuple[Vec3, Vec3] | None:
        """World bounding box of the shown parts of ``board_ids``."""
        parts = self.controller.evaluation.machining.parts
        lows: list[Vec3] = []
        highs: list[Vec3] = []
        for board_id in board_ids:
            if board_id not in self._board_actors:
                continue
            for shape in parts.get(board_id, []):
                lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
                lows.append(lo)
                highs.append(hi)
        if not lows:
            return None
        lo_arr, hi_arr = np.min(lows, axis=0), np.max(highs, axis=0)
        return (
            (float(lo_arr[0]), float(lo_arr[1]), float(lo_arr[2])),
            (float(hi_arr[0]), float(hi_arr[1]), float(hi_arr[2])),
        )

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
        for axis, direction in UNIT.items():
            arrow = pv.Arrow(
                start=(0.0, 0.0, 0.0),
                direction=direction,
                tip_length=0.25,
                tip_radius=0.08,
                shaft_radius=0.03,
            )
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(arrow)
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*_rgb(AXIS_COLORS[axis]))
            actor.GetProperty().SetAmbient(0.4)
            actor.PickableOff()
            actor.VisibilityOff()
            self.overlay.AddActor(actor)
            self._arrows[axis] = actor
        self._gizmo_origin: Vec3 | None = None
        self.renderer.AddObserver(vtkCommand.StartEvent, self._scale_gizmo)

    def gizmo_board(self) -> str | None:
        """The board the gizmo belongs to when exactly one board is selected."""
        selection = self.controller.selection
        if len(selection) == 1 and selection[0] in self._board_actors:
            return selection[0]
        return None

    def gizmo_origin(self) -> Vec3 | None:
        """Where the gizmo is drawn (None: nothing selected)."""
        return self._current_gizmo_origin()

    def _place_gizmo(self) -> None:
        box = self.bounds(self._selected_boards)
        if box is None:
            self._gizmo_origin = None
        else:
            lo, hi = box
            self._gizmo_origin = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        self._show_gizmo_at(self._gizmo_origin)

    def _show_gizmo_at(self, origin: Vec3 | None) -> None:
        for actor in self._arrows.values():
            actor.SetVisibility(origin is not None)
            if origin is not None:
                actor.SetPosition(*origin)

    def world_per_pixel(self, point: Vec3) -> float:
        return world_per_pixel(self.renderer, point)

    def _gizmo_scale(self, origin: Vec3) -> float:
        return GIZMO_SIZE_PX * self.world_per_pixel(origin)

    def _scale_gizmo(self, *_args: object) -> None:
        origin = self._current_gizmo_origin()
        if origin is None:
            return
        scale = self._gizmo_scale(origin)
        for actor in self._arrows.values():
            actor.SetScale(scale, scale, scale)

    def _current_gizmo_origin(self) -> Vec3 | None:
        o = self._gizmo_origin
        if o is None or self._drag is None:
            return o
        d = self._drag.delta
        return (o[0] + d[0], o[1] + d[1], o[2] + d[2])

    def to_display(self, point: Vec3) -> Point2:
        self.renderer.SetWorldPoint(point[0], point[1], point[2], 1.0)
        self.renderer.WorldToDisplay()
        x, y, _z = self.renderer.GetDisplayPoint()
        return float(x), float(y)

    def arrow_at(self, x: int, y: int) -> Axis | None:
        """The gizmo arrow within ``GIZMO_PICK_PX`` of the cursor (screen space)."""
        origin = self._gizmo_origin
        if origin is None:
            return None
        scale = self._gizmo_scale(origin)
        start = self.to_display(origin)
        best: tuple[float, Axis] | None = None
        for axis, unit in UNIT.items():
            end = self.to_display(
                (
                    origin[0] + unit[0] * scale,
                    origin[1] + unit[1] * scale,
                    origin[2] + unit[2] * scale,
                )
            )
            if math.hypot(end[0] - start[0], end[1] - start[1]) < 4:
                continue  # looking along this axis: it cannot be dragged sensibly
            distance = segment_distance((x, y), start, end)
            if distance <= GIZMO_PICK_PX and (best is None or distance < best[0]):
                best = (distance, axis)
        return best[1] if best else None

    def _color_arrows(self, active: Axis | None) -> None:
        for axis, actor in self._arrows.items():
            color = HIGHLIGHT_COLOR if axis == active else AXIS_COLORS[axis]
            actor.GetProperty().SetColor(*_rgb(color))

    def _ray(self, x: float, y: float) -> tuple[Vec3, Vec3]:
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

    def _axis_t(self, anchor: Vec3, axis: Axis, x: int, y: int) -> float:
        ray_start, ray_direction = self._ray(x, y)
        return axis_parameter(anchor, UNIT[axis], ray_start, ray_direction)

    def plane_point(self, anchor: Vec3, normal: int, x: float, y: float) -> Vec3 | None:
        """Where the ray under the cursor meets the plane through ``anchor``
        perpendicular to world axis ``normal`` (None if parallel or behind)."""
        start, direction = self._ray(x, y)
        if abs(direction[normal]) < 1e-6:
            return None
        t = (anchor[normal] - start[normal]) / direction[normal]
        if t < 0:
            return None
        return (
            start[0] + t * direction[0],
            start[1] + t * direction[1],
            start[2] + t * direction[2],
        )

    # ================================================================== pointer events
    def press(self, x: int, y: int, ctrl: bool) -> bool:
        self._press = (x, y)
        self._press_board = self._press_point = None
        axis = self.arrow_at(x, y)
        if axis is not None and self._gizmo_origin is not None:
            drag = self._new_drag(list(self.controller.selection), self._gizmo_origin)
            drag.axis = axis
            drag.t0 = self._axis_t(self._gizmo_origin, axis, x, y)
            self._drag = drag
            self._color_arrows(axis)
            self.viewport.render()
            return True
        board_id, point = self.pick(x, y)
        if board_id is None or point is None:
            return False  # empty space: the view orbits
        # On a part: a click selects, a drag moves (decided in move/release).
        self._press_board = board_id
        self._press_point = point
        return True

    def _new_drag(self, ids: list[str], anchor: Vec3) -> _Drag:
        doc = self.controller.doc
        moved = doc.with_contents(ids)
        boards = {b for b in moved if b in doc.boards}
        single = ids[0] if len(ids) == 1 and ids[0] in doc.boards else None
        return _Drag(ids=ids, boards=boards, single=single, anchor=anchor)

    def _start_part_drag(self, ctrl: bool) -> _Drag | None:
        board_id, point = self._press_board, self._press_point
        if board_id is None or point is None:
            return None
        if board_id not in self._selected_boards:
            selection = list(self.controller.selection) if ctrl else []
            self.controller.set_selection([*selection, board_id])
        drag = self._new_drag(list(self.controller.selection), point)
        direction = self.renderer.GetActiveCamera().GetDirectionOfProjection()
        if abs(direction[2]) >= FLOOR_DRAG_MIN_Z:
            drag.normal = 2  # on the floor plane
        else:
            drag.normal = 0 if abs(direction[0]) > abs(direction[1]) else 1
        return drag

    def move(self, x: int, y: int) -> bool:
        drag = self._drag
        if drag is None:
            press = self._press
            if self._press_board is None or press is None:
                return False
            if max(abs(x - press[0]), abs(y - press[1])) <= CLICK_TOLERANCE_PX:
                return True
            ctrl = bool(self.viewport.iren.interactor.GetControlKey())
            drag = self._drag = self._start_part_drag(ctrl)
            if drag is None:
                return False

        shift = bool(self.viewport.iren.interactor.GetShiftKey())
        evaluation = self.controller.evaluation
        raw = [0.0, 0.0, 0.0]
        if drag.axis is not None:
            raw[drag.axis.position] = self._axis_t(drag.anchor, drag.axis, x, y) - drag.t0
            axes = [drag.axis]
        else:
            hit = self.plane_point(drag.anchor, drag.normal, x, y)
            if hit is None:
                return True
            raw = [hit[i] - drag.anchor[i] for i in range(3)]
            raw[drag.normal] = 0.0
            axes = [a for a in AXES if a.position != drag.normal]
        delta = [float(round(v)) for v in raw]
        drag.snaps = {}
        if self.snapping_enabled and not shift and drag.single is not None:
            tolerance = self.snap_tolerance_px * self.world_per_pixel(drag.anchor)
            start = evaluation.boards[drag.single].origin
            for axis in axes:
                if axis not in drag.targets:
                    drag.targets[axis] = snap_targets(
                        self.controller.doc, evaluation, drag.single, axis
                    )
                i = axis.position
                snap = best_snap(drag.targets[axis], start[i] + raw[i], tolerance)
                if snap is not None:
                    drag.snaps[axis] = snap
                    delta[i] = snap.value - start[i]
        drag.delta = delta
        self._status(drag, axes)
        self._preview(drag)
        return True

    def _status(self, drag: _Drag, axes: list[Axis]) -> None:
        parts = []
        for axis in axes:
            snap = drag.snaps.get(axis)
            name = axis.value.upper()
            if snap is not None:
                parts.append(self.tr("{0} an {1}").format(name, snap.formula))
            else:
                parts.append(f"{name} {number(drag.delta[axis.position])} mm")
        self.status_message.emit(
            self.tr("Verschieben: {0}   (Umschalt: ohne Einrasten, Esc: abbrechen)").format(
                ", ".join(parts)
            )
        )

    def _preview(self, drag: _Drag) -> None:
        parts = self.controller.evaluation.machining.parts
        d = drag.delta
        for board_id in drag.boards:
            for (face, edge), shape in zip(
                self._board_actors.get(board_id, []), parts.get(board_id, []), strict=False
            ):
                o = shape.origin
                face.SetPosition(o[0] + d[0], o[1] + d[1], o[2] + d[2])
                edge.SetPosition(o[0] + d[0], o[1] + d[1], o[2] + d[2])
        self._show_gizmo_at(self._current_gizmo_origin())
        self.viewport.render()

    def release(self, x: int, y: int, ctrl: bool) -> None:
        press, self._press = self._press, None
        board_id, self._press_board = self._press_board, None
        drag, self._drag = self._drag, None
        if drag is not None:
            self._color_arrows(None)
            self.status_message.emit("")
            self._commit(drag)
            return
        if press is None or max(abs(x - press[0]), abs(y - press[1])) > CLICK_TOLERANCE_PX:
            return
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

    def hover(self, x: int, y: int) -> None:
        axis = self.arrow_at(x, y)
        if axis == self._hover_axis:
            return
        self._hover_axis = axis
        self._color_arrows(axis)
        self.viewport.render()
        if axis is not None:
            self.status_message.emit(
                self.tr("Ziehen: entlang {0} verschieben").format(axis.value.upper())
            )
        else:
            self.status_message.emit("")

    def cancel(self) -> None:
        drag, self._drag = self._drag, None
        self._press = None
        self._press_board = self._press_point = None
        if drag is not None:
            self._color_arrows(None)
            self.status_message.emit(self.tr("Verschieben abgebrochen"))
            self.rebuild()

    def context_menu(self, x: int, y: int) -> None:
        board_id = self.board_at(x, y)
        if board_id is not None and board_id not in self._selected_boards:
            self.controller.set_selection([board_id])
        ratio = self.viewport.devicePixelRatioF() or 1.0
        height = self.renderer.GetRenderWindow().GetSize()[1]
        self.context_menu_requested.emit(round(x / ratio), round((height - y - 1) / ratio))

    def _commit(self, drag: _Drag) -> None:
        if all(abs(v) < 0.5 for v in drag.delta):
            self.rebuild()  # undo the preview
            return
        undo_count = self.controller.undo_stack.count()
        if drag.single is not None:
            moves: dict[Axis, tuple[str, float]] = {}
            start = self.controller.evaluation.boards[drag.single].origin
            for axis in AXES:
                i = axis.position
                snap = drag.snaps.get(axis)
                if snap is not None:
                    moves[axis] = (snap.formula, snap.value)
                elif abs(drag.delta[i]) >= 0.5:
                    current = self.controller.doc.display_expression(drag.single, axis.value)
                    moves[axis] = (add_offset(current, drag.delta[i]), start[i] + drag.delta[i])
            self.controller.place_board(drag.single, moves)
        else:
            self.controller.translate(drag.ids, (drag.delta[0], drag.delta[1], drag.delta[2]))
        if self.controller.undo_stack.count() == undo_count:
            self.rebuild()
