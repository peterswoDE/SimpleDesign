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
* Small cubes on the faces of a single selected board resize it: the face
  moves, the opposite face stays. The change is added to the size formula.
* In rotate mode three rings replace the arrows; dragging a ring turns the
  board about its centre (15° steps, Shift: 1°).
* While dragging an arrow, handle or ring a value can be typed: distance,
  new size or angle; Enter applies it.
* Section view: one world-space clipping plane on every part mapper (the
  mappers convert it per actor) hides one side; the exact cut faces are drawn
  as caps in the material colour with a hatch texture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pyvista as pv
from PySide6.QtCore import QObject, Qt, Signal
from vtkmodules.vtkCommonCore import vtkCommand
from vtkmodules.vtkCommonDataModel import vtkPlane
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkCellPicker,
    vtkPolyDataMapper,
    vtkRenderer,
    vtkTexture,
)

from simpledesign.app.controller import Controller
from simpledesign.app.snapping import SnapTarget, Vec3, axis_parameter, best_snap, snap_targets
from simpledesign.core.geometry.collision import Collision, find_collisions
from simpledesign.core.geometry.explode import explode_offsets
from simpledesign.core.geometry.mesh import part_mesh
from simpledesign.core.geometry.placement import world_bounds
from simpledesign.core.geometry.section import SectionPlane, section_cap
from simpledesign.core.model import fit, transform
from simpledesign.core.model.machining import HardwareSolid, PartShape, hardware_solids
from simpledesign.core.model.part import Axis
from simpledesign.core.params.expression import add_offset
from simpledesign.ui.texts import number
from simpledesign.ui.tools import Tool
from simpledesign.ui.viewport import Viewport, world_per_pixel

CLICK_TOLERANCE_PX = 3
SNAP_TOLERANCE_PX = 8
GIZMO_SIZE_PX = 90
GIZMO_PICK_PX = 10  # distance from an arrow (on screen) that still grabs it
HANDLE_PX = 11  # edge length of a resize handle on screen
HANDLE_PICK_PX = 9
HANDLE_MIN_SPREAD_PX = 14  # hide handles this close to the centre (thin side)
RING_PX = 70  # radius of a rotate ring on screen
RING_SAMPLES = 72
ROTATE_STEP = 15.0  # degrees (Shift: 1°)
RING_EDGE_ON = 0.25  # |view · axis| below this: ring seen edge-on, use screen angle
SELECTION_COLOR = "#2f7de1"
COLLISION_COLOR = "#e02020"
EDGE_COLOR = "#4b4136"
TRANSPARENT_OPACITY = 0.3
AXIS_COLORS = {Axis.X: "#d64545", Axis.Y: "#3fa34d", Axis.Z: "#3b6fd6"}
HIGHLIGHT_COLOR = "#ffcc33"
HATCH_SPACING = 8.0  # mm between hatch lines on cut faces
HATCH_TEXELS = 32
UNIT = {Axis.X: (1.0, 0.0, 0.0), Axis.Y: (0.0, 1.0, 0.0), Axis.Z: (0.0, 0.0, 1.0)}
AXES = (Axis.X, Axis.Y, Axis.Z)
# Looking down at least this steeply (|view direction · Z|) drags parts on the floor plane.
FLOOR_DRAG_MIN_Z = 0.35

type Size = tuple[float, float, float]
type Point2 = tuple[float, float]


HARDWARE_COLOR = "#a7adb4"  # brushed steel


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


def _circle(axis: Axis) -> pv.PolyData:
    """Unit circle around ``axis`` through the origin (closed polyline)."""
    i = axis.position
    u, v = (i + 1) % 3, (i + 2) % 3
    points = np.zeros((RING_SAMPLES, 3))
    angles = np.linspace(0.0, 2 * math.pi, RING_SAMPLES, endpoint=False)
    points[:, u] = np.cos(angles)
    points[:, v] = np.sin(angles)
    lines = np.array([RING_SAMPLES + 1, *range(RING_SAMPLES), 0], dtype=np.int64)
    return pv.PolyData(points, lines=lines)


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
    kind: str = "move"  # "move", "resize" or "rotate"
    side: int = 0  # resize: +1 end face, -1 start face
    faces: list[float] = field(default_factory=list)  # resize: faces to snap to
    snapped: bool = False
    # rotate: cursor angle last time, accumulated turn, 0 = measured in the
    # ring plane, ±1 = on screen (sign towards the viewer)
    last_angle: float = 0.0
    turned: float = 0.0
    sense: float = 1.0


class Scene(QObject):
    status_message = Signal(str)
    tool_changed = Signal()  # a click tool started or ended
    view_mode_changed = Signal()  # isolation or X-ray switched
    # Right click at a viewport position (logical pixels from the top-left).
    context_menu_requested = Signal(int, int)
    collisions_changed = Signal()  # the set of overlapping parts changed

    def __init__(self, viewport: Viewport, controller: Controller) -> None:
        super().__init__(viewport)
        self.viewport = viewport
        self.controller = controller
        self.renderer: vtkRenderer = viewport.renderer
        self.snapping_enabled = True
        self.snap_tolerance_px = SNAP_TOLERANCE_PX
        self._rotate_mode = False

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
        self._typed = ""  # value typed during a drag
        self.tool: Tool | None = None
        # Isolation: only these boards (and boards made later) are shown.
        self._isolated: set[str] | None = None
        self._known: set[str] = set()
        self._xray = False
        self._explode = 0.0  # exploded view: 0 = assembled
        self._offsets: dict[str, list[Vec3]] = {}  # exploded display offsets
        self._section: SectionPlane | None = None
        self._clip = vtkPlane()
        self._show_collisions = False
        self.collisions: list[Collision] = []
        # fittings (hinges, slides, handles) as simple metal bodies
        self._show_hardware = True
        self.hardware: list[tuple[HardwareSolid, vtkActor]] = []
        self._hardware_pool: list[vtkActor] = []
        self._hardware_mappers: dict[tuple[int, Vec3], vtkPolyDataMapper] = {}
        self._solids_of: tuple[object, list[HardwareSolid]] | None = None
        self._collision_key: tuple[int, frozenset[str]] | None = None

        self._create_overlay()
        self._create_caps()
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
            if self._section is not None:
                face_mapper.AddClippingPlane(self._clip)
                edge_mapper.AddClippingPlane(self._clip)
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

    # ================================================================== view modes
    @property
    def isolated(self) -> bool:
        return self._isolated is not None

    def isolate(self, board_ids: set[str] | None) -> None:
        """Show only ``board_ids`` (None: everything again)."""
        if board_ids is not None and not board_ids:
            board_ids = None
        self._isolated = board_ids
        self._known = set(self.controller.doc.boards) if board_ids is not None else set()
        self.rebuild()
        self.view_mode_changed.emit()

    def _hidden_by_isolation(self, board_id: str) -> bool:
        return (
            self._isolated is not None
            and board_id not in self._isolated
            and board_id in self._known
        )

    @property
    def xray(self) -> bool:
        return self._xray

    @xray.setter
    def xray(self, on: bool) -> None:
        if on != self._xray:
            self._xray = on
            self.rebuild()
            self.view_mode_changed.emit()

    @property
    def explode(self) -> float:
        return self._explode

    @explode.setter
    def explode(self, factor: float) -> None:
        """Pull the parts apart for display (0 = assembled, 1 = twice as far
        from the centre). While exploded, parts can be selected but not moved."""
        factor = max(0.0, min(2.0, factor))
        if factor != self._explode:
            was = self.exploded
            self._explode = factor
            self.rebuild()
            if was != self.exploded:
                self.view_mode_changed.emit()

    @property
    def exploded(self) -> bool:
        return self._explode > 0

    @property
    def section(self) -> SectionPlane | None:
        return self._section

    @section.setter
    def section(self, plane: SectionPlane | None) -> None:
        """Cut the view open (None: whole parts again)."""
        if plane == self._section:
            return
        was = self._section is not None
        self._section = plane
        self._update_section()
        self.viewport.render()
        if was != (plane is not None):
            self.view_mode_changed.emit()

    def _update_section(self) -> None:
        plane = self._section
        mappers = [m for pair in self._meshes.values() for m in pair]
        mappers.append(self._collision_mapper)
        mappers.extend(self._hardware_mappers.values())
        for mapper in mappers:
            mapper.RemoveAllClippingPlanes()
        if plane is None:
            self._cap_actor.VisibilityOff()
            self._cap_edge_actor.VisibilityOff()
            return
        self._clip.SetOrigin(*plane.origin)
        self._clip.SetNormal(*plane.normal)
        for mapper in mappers:
            mapper.AddClippingPlane(self._clip)
        doc = self.controller.doc
        evaluation = self.controller.evaluation
        caps = []
        for board_id in self._board_actors:
            board = doc.boards[board_id]
            color = (
                SELECTION_COLOR
                if board_id in self._selected_boards
                else doc.materials[board.material_id].color
            )
            rgb = np.array([round(c * 255) for c in _rgb(color)], dtype=np.uint8)
            moved = self._offsets.get(board_id)
            for copy, shape in enumerate(evaluation.machining.parts[board_id]):
                cap = section_cap(shape, plane, moved[copy] if moved else (0.0, 0.0, 0.0))
                if cap is None:
                    continue
                mesh = pv.PolyData(cap.points, cap.faces)
                mesh.point_data["rgb"] = np.tile(rgb, (mesh.n_points, 1))
                caps.append(mesh)
        if not caps:
            self._cap_actor.VisibilityOff()
            self._cap_edge_actor.VisibilityOff()
            return
        merged = caps[0] if len(caps) == 1 else pv.merge(caps, merge_points=False)
        u, v = (plane.axis + 1) % 3, (plane.axis + 2) % 3
        merged.active_texture_coordinates = merged.points[:, [u, v]] / HATCH_SPACING
        self._cap_mapper.SetInputData(merged)
        self._cap_mapper.SelectColorArray("rgb")
        outline = merged.extract_feature_edges(
            boundary_edges=True, feature_edges=False, manifold_edges=False, non_manifold_edges=False
        )
        self._cap_edge_mapper.SetInputData(outline)
        self._cap_actor.VisibilityOn()
        self._cap_edge_actor.VisibilityOn()

    def _create_caps(self) -> None:
        n = HATCH_TEXELS
        i, j = np.mgrid[0:n, 0:n]
        # one soft diagonal line per tile: dark on white, times the vertex colour
        d = np.minimum((i + j) % n, n - (i + j) % n)
        shade = np.clip(70 + (d - 1.5) * 90, 70, 255).astype(np.uint8)
        image = pv.ImageData(dimensions=(n, n, 1))
        image.point_data["texels"] = np.repeat(shade.reshape(-1, 1), 3, axis=1)
        image.set_active_scalars("texels")
        texture = vtkTexture()
        texture.SetInputData(image)
        texture.RepeatOn()
        texture.InterpolateOn()
        self._cap_mapper = vtkPolyDataMapper()
        self._cap_mapper.SetScalarModeToUsePointFieldData()
        self._cap_mapper.SetColorModeToDirectScalars()
        self._cap_actor = vtkActor()
        self._cap_actor.SetMapper(self._cap_mapper)
        self._cap_actor.SetTexture(texture)
        self._cap_actor.GetProperty().SetLighting(False)
        self._cap_actor.PickableOff()
        self._cap_actor.VisibilityOff()
        self._cap_edge_mapper = vtkPolyDataMapper()
        self._cap_edge_mapper.ScalarVisibilityOff()
        self._cap_edge_actor = vtkActor()
        self._cap_edge_actor.SetMapper(self._cap_edge_mapper)
        edge_prop = self._cap_edge_actor.GetProperty()
        edge_prop.SetColor(*_rgb(EDGE_COLOR))
        edge_prop.SetLighting(False)
        edge_prop.SetLineWidth(1.5)
        self._cap_edge_actor.PickableOff()
        self._cap_edge_actor.VisibilityOff()
        self.renderer.AddActor(self._cap_actor)
        self.renderer.AddActor(self._cap_edge_actor)

    @property
    def show_collisions(self) -> bool:
        return self._show_collisions

    @show_collisions.setter
    def show_collisions(self, on: bool) -> None:
        if on != self._show_collisions:
            self._show_collisions = on
            self._collision_key = None
            self.rebuild()
            self.view_mode_changed.emit()

    def _update_collisions(self, visible: set[str]) -> None:
        """Recompute the overlaps of the visible parts and show them in red."""
        if not self._show_collisions:
            self._collision_actor.VisibilityOff()
            if self.collisions:
                self.collisions = []
                self.collisions_changed.emit()
            return
        evaluation = self.controller.evaluation
        key = (id(evaluation), frozenset(visible))
        if key == self._collision_key:
            return
        first = self._collision_key is None  # just switched on: always report
        self._collision_key = key
        parts = {b: evaluation.machining.parts[b] for b in visible}
        found = find_collisions(parts)
        changed = [(c.first, c.second) for c in found] != [
            (c.first, c.second) for c in self.collisions
        ]
        self.collisions = found
        if found:
            meshes = [pv.PolyData(c.mesh.points, c.mesh.faces) for c in found]
            merged = meshes[0] if len(meshes) == 1 else pv.merge(meshes)
            self._collision_mapper.SetInputData(merged)
            self._collision_actor.VisibilityOn()
        else:
            self._collision_actor.VisibilityOff()
        if changed or first:
            self.collisions_changed.emit()

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
        if self._isolated is not None and not self._isolated & doc.boards.keys():
            self._isolated = None  # isolated parts gone (new file, deleted)
            self.view_mode_changed.emit()
        offsets = (
            explode_offsets(
                doc,
                evaluation,
                self._explode,
                {
                    b.id
                    for b in doc.boards.values()
                    if doc.is_visible(b) and not self._hidden_by_isolation(b.id)
                },
            )
            if self._explode > 0
            else {}
        )
        self._offsets = offsets
        for board in doc.boards.values():
            if not doc.is_visible(board) or self._hidden_by_isolation(board.id):
                continue
            is_selected = board.id in selected
            color = SELECTION_COLOR if is_selected else doc.materials[board.material_id].color
            pairs = []
            moved = offsets.get(board.id)
            for copy, shape in enumerate(evaluation.machining.parts[board.id]):
                face_mapper, edge_mapper = self._mappers(shape)
                origin = shape.origin
                if moved:
                    d = moved[copy]
                    origin = (origin[0] + d[0], origin[1] + d[1], origin[2] + d[2])
                face, edge = self._pooled(used)
                used += 1
                face.SetMapper(face_mapper)
                edge.SetMapper(edge_mapper)
                for actor in (face, edge):
                    actor.SetOrientation(0.0, 0.0, 0.0)
                    actor.SetScale(1.0, 1.0, 1.0)
                    actor.SetUserTransform(None)
                    actor.SetPosition(*origin)
                    actor.VisibilityOn()
                prop = face.GetProperty()
                prop.SetColor(*_rgb(color))
                see_through = board.transparent or (self._xray and not is_selected)
                prop.SetOpacity(TRANSPARENT_OPACITY if see_through else 1.0)
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
        self._update_hardware()
        self._update_collisions(set() if self.exploded else set(self._board_actors))
        if self._section is not None:
            self._update_section()
        self._place_gizmo()
        if self.tool is not None:
            self.tool.scene_rebuilt()
        self.viewport.render()

    # ================================================================ hardware
    @property
    def show_hardware(self) -> bool:
        return self._show_hardware

    @show_hardware.setter
    def show_hardware(self, on: bool) -> None:
        if on != self._show_hardware:
            self._show_hardware = on
            self.rebuild()

    def hardware_solids(self) -> list[HardwareSolid]:
        """Metal parts of all fittings (cached per evaluation)."""
        evaluation = self.controller.evaluation
        if self._solids_of is None or self._solids_of[0] is not evaluation:
            solids = hardware_solids(evaluation.boards, self.controller.doc.fittings.values())
            self._solids_of = (evaluation, solids)
        return self._solids_of[1]

    def _hardware_mapper(self, solid: HardwareSolid) -> vtkPolyDataMapper:
        size = (
            round(solid.hi[0] - solid.lo[0], 2),
            round(solid.hi[1] - solid.lo[1], 2),
            round(solid.hi[2] - solid.lo[2], 2),
        )
        key = (solid.axis, size)
        if key not in self._hardware_mappers:
            if solid.axis < 0:
                mesh = pv.Box(bounds=(0.0, size[0], 0.0, size[1], 0.0, size[2]))
            else:
                direction = [0.0, 0.0, 0.0]
                direction[solid.axis] = 1.0
                others = [size[i] for i in range(3) if i != solid.axis]
                mesh = pv.Cylinder(
                    center=(size[0] / 2, size[1] / 2, size[2] / 2),
                    direction=direction,
                    radius=min(others) / 2,
                    height=size[solid.axis],
                    resolution=24,
                )
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(mesh)
            if self._section is not None:
                mapper.AddClippingPlane(self._clip)
            self._hardware_mappers[key] = mapper
        return self._hardware_mappers[key]

    def _update_hardware(self) -> None:
        self.hardware = []
        if len(self._hardware_mappers) > 256:
            self._hardware_mappers.clear()
        used = 0
        if self._show_hardware:
            for solid in self.hardware_solids():
                if solid.board_id not in self._board_actors:
                    continue  # its part is hidden
                if used == len(self._hardware_pool):
                    actor = vtkActor()
                    actor.PickableOff()
                    prop = actor.GetProperty()
                    prop.SetColor(*_rgb(HARDWARE_COLOR))
                    prop.SetAmbient(0.3)
                    prop.SetDiffuse(0.6)
                    prop.SetSpecular(0.6)
                    prop.SetSpecularPower(30)
                    self.renderer.AddActor(actor)
                    self._hardware_pool.append(actor)
                actor = self._hardware_pool[used]
                used += 1
                actor.SetMapper(self._hardware_mapper(solid))
                moved = self._offsets.get(solid.board_id)
                d = moved[solid.copy] if moved else (0.0, 0.0, 0.0)
                actor.SetPosition(solid.lo[0] + d[0], solid.lo[1] + d[1], solid.lo[2] + d[2])
                actor.SetUserTransform(None)
                actor.VisibilityOn()
                self.hardware.append((solid, actor))
        for actor in self._hardware_pool[used:]:
            actor.VisibilityOff()

    def instance_actors(self, board_id: str, copy: int) -> list[vtkActor]:
        """Face, edge and hardware actors of one copy of a board."""
        result: list[vtkActor] = []
        pairs = self._board_actors.get(board_id, [])
        if copy < len(pairs):
            result.extend(pairs[copy])
        result.extend(a for s, a in self.hardware if s.board_id == board_id and s.copy == copy)
        return result

    def instance_at(self, x: int, y: int) -> tuple[str, int] | None:
        """Board and copy under the pointer."""
        if not self._picker.Pick(x, y, 0, self.renderer):
            return None
        actor = self._picker.GetActor()
        if actor is None:
            return None
        board_id = self._actor_board.get(id(actor))
        if board_id is None:
            return None
        for copy, (face, _edge) in enumerate(self._board_actors.get(board_id, [])):
            if face is actor:
                return board_id, copy
        return None

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

    def pick_face(self, x: int, y: int) -> tuple[str, Vec3, int, int] | None:
        """Board, hit point, normal axis and its sign of the face under the
        cursor (only faces along a world axis)."""
        board_id, point = self.pick(x, y)
        if board_id is None or point is None:
            return None
        normal = self._picker.GetPickNormal()
        axis = max(range(3), key=lambda i: abs(normal[i]))
        if abs(normal[axis]) < 0.9:
            return None
        return board_id, point, axis, 1 if normal[axis] > 0 else -1

    # ================================================================== tools
    def start_tool(self, tool: Tool) -> None:
        """Let ``tool`` take the pointer events until it ends (Esc, right click)."""
        self.end_tool(notify=False)
        self.cancel()
        self.tool = tool
        self.viewport.interaction_style.pointer_handler = tool
        tool.start()
        self.tool_changed.emit()

    def end_tool(self, notify: bool = True) -> None:
        tool, self.tool = self.tool, None
        if tool is None:
            return
        tool.finish()
        self.viewport.interaction_style.pointer_handler = self
        if notify:
            self.status_message.emit("")
            self.tool_changed.emit()

    def bounds(self, board_ids: set[str]) -> tuple[Vec3, Vec3] | None:
        """World bounding box of the shown parts of ``board_ids``."""
        parts = self.controller.evaluation.machining.parts
        lows: list[Vec3] = []
        highs: list[Vec3] = []
        for board_id in board_ids:
            if board_id not in self._board_actors:
                continue
            moved = self._offsets.get(board_id)
            for copy, shape in enumerate(parts.get(board_id, [])):
                lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
                if moved:
                    d = moved[copy]
                    lo = (lo[0] + d[0], lo[1] + d[1], lo[2] + d[2])
                    hi = (hi[0] + d[0], hi[1] + d[1], hi[2] + d[2])
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

        # Overlapping volumes: drawn on top so they show inside the parts.
        self._collision_mapper = vtkPolyDataMapper()
        self._collision_actor = vtkActor()
        self._collision_actor.SetMapper(self._collision_mapper)
        self._collision_actor.PickableOff()
        self._collision_actor.VisibilityOff()
        collision_prop = self._collision_actor.GetProperty()
        collision_prop.SetColor(*_rgb(COLLISION_COLOR))
        collision_prop.SetOpacity(0.7)
        self.overlay.AddActor(self._collision_actor)

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

        cube = pv.Cube(center=(0.0, 0.0, 0.0), x_length=1.0, y_length=1.0, z_length=1.0)
        self._handles: dict[tuple[Axis, int], vtkActor] = {}
        self._handle_points: dict[tuple[Axis, int], Vec3] = {}
        for axis in AXES:
            for side in (-1, 1):
                mapper = vtkPolyDataMapper()
                mapper.SetInputData(cube)
                actor = vtkActor()
                actor.SetMapper(mapper)
                actor.GetProperty().SetColor(*_rgb(AXIS_COLORS[axis]))
                actor.GetProperty().SetAmbient(0.6)
                actor.PickableOff()
                actor.VisibilityOff()
                self.overlay.AddActor(actor)
                self._handles[axis, side] = actor
        self._hover_handle: tuple[Axis, int] | None = None

        self._rings: dict[Axis, vtkActor] = {}
        for axis in AXES:
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(_circle(axis))
            actor = vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetColor(*_rgb(AXIS_COLORS[axis]))
            prop.SetLineWidth(3.0)
            prop.SetLighting(False)
            actor.PickableOff()
            actor.VisibilityOff()
            self.overlay.AddActor(actor)
            self._rings[axis] = actor
        self._hover_ring: Axis | None = None
        self.renderer.AddObserver(vtkCommand.StartEvent, self._scale_gizmo)

    @property
    def rotate_mode(self) -> bool:
        """Rings instead of arrows and handles: drag a ring to turn the board."""
        return self._rotate_mode

    @rotate_mode.setter
    def rotate_mode(self, on: bool) -> None:
        if on == self._rotate_mode:
            return
        self.cancel()
        self._rotate_mode = on
        self._place_gizmo()
        self.viewport.render()
        self.status_message.emit(
            self.tr("Drehen: einen Ring ziehen (15°-Schritte, Umschalt: 1°)") if on else ""
        )

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
        box = None if self.exploded else self.bounds(self._selected_boards)
        if box is None:
            self._gizmo_origin = None
        else:
            lo, hi = box
            self._gizmo_origin = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        self._show_gizmo_at(self._gizmo_origin)
        self._place_handles()
        self._place_rings()

    def _place_handles(self) -> None:
        """Resize handles on the face centres of a single, not rotated board."""
        self._handle_points = {}
        board_id = None if self._rotate_mode or self.exploded else self.gizmo_board()
        doc, evaluation = self.controller.doc, self.controller.evaluation
        state = evaluation.boards.get(board_id) if board_id is not None else None
        if board_id is not None and state is not None and not state.rotated:
            lo, size = state.origin, state.size
            centre = [lo[i] + size[i] / 2 for i in range(3)]
            for axis in AXES:
                if not transform.resizable(doc, evaluation, board_id, axis):
                    continue
                i = axis.position
                for side in (-1, 1):
                    point = list(centre)
                    point[i] = lo[i] + (size[i] if side > 0 else 0.0)
                    self._handle_points[axis, side] = (point[0], point[1], point[2])
        for key, actor in self._handles.items():
            spot = self._handle_points.get(key)
            actor.SetVisibility(spot is not None)
            if spot is not None:
                actor.SetPosition(*spot)

    def ring_axes(self) -> list[Axis]:
        """The rings that can be dragged: all three, or only the axis an
        already rotated board turns about."""
        board_id = self.gizmo_board() if self._rotate_mode else None
        if board_id is None or self._gizmo_origin is None:
            return []
        state = self.controller.evaluation.boards.get(board_id)
        if state is None:
            return []
        board = self.controller.doc.boards[board_id]
        return [board.rotation_axis] if state.rotated else list(AXES)

    def _place_rings(self) -> None:
        axes = self.ring_axes()
        for axis, actor in self._rings.items():
            actor.SetVisibility(axis in axes)
            if axis in axes and self._gizmo_origin is not None:
                actor.SetPosition(*self._gizmo_origin)

    def _show_gizmo_at(self, origin: Vec3 | None) -> None:
        for actor in self._arrows.values():
            actor.SetVisibility(origin is not None and not self._rotate_mode)
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
        for actor in self._handles.values():
            if actor.GetVisibility():
                size = HANDLE_PX * self.world_per_pixel(actor.GetPosition())
                actor.SetScale(size, size, size)
        radius = RING_PX * self.world_per_pixel(origin)
        for actor in self._rings.values():
            actor.SetScale(radius, radius, radius)

    def _current_gizmo_origin(self) -> Vec3 | None:
        o = self._gizmo_origin
        if o is None or self._drag is None or self._drag.kind != "move":
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
        if origin is None or self._rotate_mode:
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

    def handle_at(self, x: int, y: int) -> tuple[Axis, int] | None:
        """The resize handle (axis, side) under the cursor (screen space)."""
        origin = self._gizmo_origin
        if origin is None or not self._handle_points:
            return None
        centre = self.to_display(origin)
        best: tuple[float, tuple[Axis, int]] | None = None
        for key, point in self._handle_points.items():
            px, py = self.to_display(point)
            if math.hypot(px - centre[0], py - centre[1]) < HANDLE_MIN_SPREAD_PX:
                continue  # looking at the thin side: too close to the centre
            distance = math.hypot(x - px, y - py)
            if distance <= HANDLE_PICK_PX and (best is None or distance < best[0]):
                best = (distance, key)
        return best[1] if best else None

    def ring_at(self, x: int, y: int) -> Axis | None:
        """The rotate ring within ``GIZMO_PICK_PX`` of the cursor (screen space)."""
        origin = self._gizmo_origin
        if origin is None:
            return None
        radius = RING_PX * self.world_per_pixel(origin)
        best: tuple[float, Axis] | None = None
        for axis in self.ring_axes():
            i = axis.position
            u, v = (i + 1) % 3, (i + 2) % 3
            screen = []
            for k in range(RING_SAMPLES):
                angle = 2 * math.pi * k / RING_SAMPLES
                point = list(origin)
                point[u] += radius * math.cos(angle)
                point[v] += radius * math.sin(angle)
                screen.append(self.to_display((point[0], point[1], point[2])))
            distance = min(
                segment_distance((x, y), screen[k], screen[(k + 1) % RING_SAMPLES])
                for k in range(RING_SAMPLES)
            )
            if distance <= GIZMO_PICK_PX and (best is None or distance < best[0]):
                best = (distance, axis)
        return best[1] if best else None

    def ring_point(self, axis: Axis, degrees: float) -> Vec3:
        """A point on a ring (for tests and hit checks)."""
        assert self._gizmo_origin is not None
        origin = self._gizmo_origin
        radius = RING_PX * self.world_per_pixel(origin)
        i = axis.position
        u, v = (i + 1) % 3, (i + 2) % 3
        point = list(origin)
        point[u] += radius * math.cos(math.radians(degrees))
        point[v] += radius * math.sin(math.radians(degrees))
        return point[0], point[1], point[2]

    def _color_rings(self, active: Axis | None) -> None:
        for axis, actor in self._rings.items():
            color = HIGHLIGHT_COLOR if axis == active else AXIS_COLORS[axis]
            actor.GetProperty().SetColor(*_rgb(color))

    def _color_arrows(self, active: Axis | None) -> None:
        for axis, actor in self._arrows.items():
            color = HIGHLIGHT_COLOR if axis == active else AXIS_COLORS[axis]
            actor.GetProperty().SetColor(*_rgb(color))

    def _color_handles(self, active: tuple[Axis, int] | None) -> None:
        for key, actor in self._handles.items():
            color = HIGHLIGHT_COLOR if key == active else AXIS_COLORS[key[0]]
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
        self._typed = ""
        self._press_board = self._press_point = None
        ring = self.ring_at(x, y)
        board_id = self.gizmo_board()
        if ring is not None and board_id is not None:
            self._drag = self._rotate_drag(board_id, ring, x, y)
            self._color_rings(ring)
            self.viewport.render()
            return True
        handle = self.handle_at(x, y)
        if handle is not None and board_id is not None:
            self._drag = self._resize_drag(board_id, handle, x, y)
            self._color_handles(handle)
            self.viewport.render()
            return True
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

    def _resize_drag(self, board_id: str, handle: tuple[Axis, int], x: int, y: int) -> _Drag:
        axis, side = handle
        anchor = self._handle_points[handle]
        drag = _Drag(ids=[board_id], boards={board_id}, single=board_id, anchor=anchor)
        drag.kind, drag.axis, drag.side = "resize", axis, side
        drag.t0 = self._axis_t(anchor, axis, x, y)
        i = axis.position
        doc, evaluation = self.controller.doc, self.controller.evaluation
        faces = {0.0}
        for box in fit.boxes(doc, evaluation, exclude=[board_id]):
            faces.update((box.lo[i], box.hi[i]))
        drag.faces = sorted(faces)
        return drag

    def _ring_angle(self, drag: _Drag, x: float, y: float) -> float:
        """Angle of the cursor about the ring centre, in degrees about the axis.

        Measured in the ring plane (exact in perspective); when looking at the
        ring edge-on, the angle on screen is used instead (``drag.sense``)."""
        assert drag.axis is not None
        centre = drag.anchor
        if drag.sense == 0.0:
            i = drag.axis.position
            hit = self.plane_point(centre, i, x, y)
            if hit is not None:
                u, v = (i + 1) % 3, (i + 2) % 3
                return math.degrees(math.atan2(hit[v] - centre[v], hit[u] - centre[u]))
        cx, cy = self.to_display(centre)
        return (drag.sense or 1.0) * math.degrees(math.atan2(y - cy, x - cx))

    def _rotate_drag(self, board_id: str, axis: Axis, x: int, y: int) -> _Drag:
        assert self._gizmo_origin is not None
        drag = _Drag(ids=[board_id], boards={board_id}, single=board_id, anchor=self._gizmo_origin)
        drag.kind, drag.axis = "rotate", axis
        view = self.renderer.GetActiveCamera().GetDirectionOfProjection()
        if abs(view[axis.position]) >= RING_EDGE_ON:
            drag.sense = 0.0  # angle in the ring plane
        else:
            # Counter-clockwise on screen is a positive turn when the axis
            # points towards the viewer (display y points up).
            drag.sense = 1.0 if view[axis.position] < 0 else -1.0
        drag.last_angle = self._ring_angle(drag, x, y)
        return drag

    def _move_rotate(self, drag: _Drag, x: int, y: int, shift: bool) -> None:
        angle = self._ring_angle(drag, x, y)
        step = (angle - drag.last_angle + 180.0) % 360.0 - 180.0
        drag.last_angle = angle
        drag.turned += step
        unit = 1.0 if shift else ROTATE_STEP
        self._set_rotate(drag, round(drag.turned / unit) * unit)

    def _set_rotate(self, drag: _Drag, degrees: float) -> None:
        assert drag.axis is not None and drag.single is not None
        drag.delta = [0.0, 0.0, 0.0]
        drag.delta[drag.axis.position] = degrees
        state = self.controller.evaluation.boards[drag.single]
        total = (state.angle if state.rotated else 0.0) + degrees
        self.status_message.emit(
            self.tr(
                "Drehen um {0}: {1:+g}° (gesamt {2:g}°)   (Umschalt: 1°, Esc: abbrechen)"
            ).format(drag.axis.value.upper(), round(degrees, 3), round(total % 360.0, 3))
        )
        self._preview_rotate(drag)

    def _preview_rotate(self, drag: _Drag) -> None:
        assert drag.axis is not None and drag.single is not None
        degrees = drag.delta[drag.axis.position]
        parts = self.controller.evaluation.machining.parts
        for (face, edge), shape in zip(
            self._board_actors.get(drag.single, []), parts.get(drag.single, []), strict=False
        ):
            lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
            centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
            turn = vtkTransform()
            turn.PostMultiply()
            turn.Translate(-centre[0], -centre[1], -centre[2])
            turn.RotateWXYZ(degrees, *UNIT[drag.axis])
            turn.Translate(*centre)
            face.SetUserTransform(turn)
            edge.SetUserTransform(turn)
        self.viewport.render()

    def _new_drag(self, ids: list[str], anchor: Vec3) -> _Drag:
        doc = self.controller.doc
        moved = doc.with_contents(ids)
        boards = {b for b in moved if b in doc.boards}
        single = ids[0] if len(ids) == 1 and ids[0] in doc.boards else None
        return _Drag(ids=ids, boards=boards, single=single, anchor=anchor)

    def _start_part_drag(self, ctrl: bool) -> _Drag | None:
        board_id, point = self._press_board, self._press_point
        if board_id is None or point is None or self.exploded:
            return None  # exploded parts are only shown apart: no moving
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
        if drag is not None and self._typed:
            return True  # a typed value wins over the mouse
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
        if drag.kind == "resize":
            self._move_resize(drag, x, y, shift)
            return True
        if drag.kind == "rotate":
            self._move_rotate(drag, x, y, shift)
            return True
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

    def _move_resize(self, drag: _Drag, x: int, y: int, shift: bool) -> None:
        assert drag.axis is not None and drag.single is not None
        i = drag.axis.position
        raw = self._axis_t(drag.anchor, drag.axis, x, y) - drag.t0
        face = drag.anchor[i]
        delta = float(round(raw))
        drag.snapped = False
        if self.snapping_enabled and not shift and drag.faces:
            tolerance = self.snap_tolerance_px * self.world_per_pixel(drag.anchor)
            target = min(drag.faces, key=lambda v: abs(v - face - raw))
            if abs(target - face - raw) <= tolerance:
                delta = target - face
                drag.snapped = True
        self._set_resize(drag, delta)

    def _set_resize(self, drag: _Drag, delta: float) -> None:
        assert drag.axis is not None and drag.single is not None
        i = drag.axis.position
        extent = self.controller.evaluation.boards[drag.single].extent(drag.axis)
        if extent + drag.side * delta < transform.MIN_SIZE:
            delta = drag.side * (transform.MIN_SIZE - extent)
        drag.delta = [0.0, 0.0, 0.0]
        drag.delta[i] = delta
        size = extent + drag.side * delta
        note = self.tr(" (eingerastet)") if drag.snapped else ""
        self.status_message.emit(
            self.tr(
                "Größe {0}: {1} mm ({2:+g}){3}   (Umschalt: ohne Einrasten, Esc: abbrechen)"
            ).format(drag.axis.value.upper(), number(size), round(delta, 3), note)
        )
        self._preview_resize(drag, extent)

    def _preview_resize(self, drag: _Drag, extent: float) -> None:
        assert drag.axis is not None and drag.single is not None
        i = drag.axis.position
        delta = drag.delta[i]
        factor = (extent + drag.side * delta) / extent if extent > 1e-9 else 1.0
        scale = [1.0, 1.0, 1.0]
        scale[i] = factor
        parts = self.controller.evaluation.machining.parts
        for (face, edge), shape in zip(
            self._board_actors.get(drag.single, []), parts.get(drag.single, []), strict=False
        ):
            origin = list(shape.origin)
            if drag.side < 0:
                origin[i] += delta
            for actor in (face, edge):
                actor.SetScale(*scale)
                actor.SetPosition(*origin)
        handle = self._handles[drag.axis, drag.side]
        point = list(drag.anchor)
        point[i] += delta
        handle.SetPosition(*point)
        self.viewport.render()

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
        if self._drag is not None:
            self._finish()
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

    def _finish(self) -> None:
        """End the running drag and apply it."""
        drag, self._drag = self._drag, None
        self._press = None
        self._press_board = self._press_point = None
        self._typed = ""
        if drag is None:
            return
        self._color_arrows(None)
        self._color_handles(None)
        self._color_rings(None)
        self.status_message.emit("")
        self._commit(drag)

    def typeable(self) -> bool:
        """A drag is running that takes a typed value (not a free plane move)."""
        drag = self._drag
        return drag is not None and drag.axis is not None

    def type_key(self, key: int, text: str) -> bool:
        if not self.typeable():
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
            return True
        if key == Qt.Key.Key_Backspace:
            self._typed = self._typed[:-1]
        elif text and text in "0123456789.,-":
            self._typed += "." if text == "," else text
        else:
            return False
        self._apply_typed()
        return True

    def _apply_typed(self) -> None:
        drag = self._drag
        assert drag is not None and drag.axis is not None
        try:
            value = float(self._typed)
        except ValueError:
            value = None
        if value is not None:
            i = drag.axis.position
            if drag.kind == "resize":
                assert drag.single is not None
                extent = self.controller.evaluation.boards[drag.single].extent(drag.axis)
                drag.snapped = False
                self._set_resize(drag, drag.side * (value - extent))
            elif drag.kind == "rotate":
                self._set_rotate(drag, value)
            else:
                drag.snaps = {}
                drag.delta = [0.0, 0.0, 0.0]
                drag.delta[i] = value
                self._preview(drag)
        what = {"resize": self.tr("neue Größe"), "rotate": self.tr("Winkel")}.get(
            drag.kind, self.tr("Abstand")
        )
        unit = "°" if drag.kind == "rotate" else " mm"
        self.status_message.emit(
            self.tr("Eingabe {0}: {1}{2}   (Enter: übernehmen, Esc: abbrechen)").format(
                what, self._typed or "…", unit
            )
        )

    def hover(self, x: int, y: int) -> None:
        if self._rotate_mode:
            ring = self.ring_at(x, y)
            if ring != self._hover_ring:
                self._hover_ring = ring
                self._color_rings(ring)
                self.viewport.render()
                self.status_message.emit(
                    self.tr("Ziehen: um {0} drehen").format(ring.value.upper())
                    if ring is not None
                    else self.tr("Drehen: einen Ring ziehen (15°-Schritte, Umschalt: 1°)")
                )
            return
        handle = self.handle_at(x, y)
        if handle != self._hover_handle:
            self._hover_handle = handle
            self._color_handles(handle)
            self.viewport.render()
            if handle is not None:
                self.status_message.emit(
                    self.tr("Ziehen: Größe entlang {0} ändern").format(handle[0].value.upper())
                )
            else:
                self.status_message.emit("")
        if handle is not None:
            return
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
        self._typed = ""
        if drag is not None:
            self._color_arrows(None)
            self._color_handles(None)
            self._color_rings(None)
            self.status_message.emit(self.tr("Abgebrochen"))
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
        if drag.kind == "resize":
            assert drag.axis is not None and drag.single is not None
            delta = drag.delta[drag.axis.position]
            self.controller.resize_board(drag.single, drag.axis, drag.side, delta)
        elif drag.kind == "rotate":
            assert drag.axis is not None and drag.single is not None
            degrees = drag.delta[drag.axis.position]
            if not self.controller.rotate_board(drag.single, drag.axis, degrees):
                self.status_message.emit(self.tr("Drehen nicht möglich"))
        elif drag.single is not None:
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
