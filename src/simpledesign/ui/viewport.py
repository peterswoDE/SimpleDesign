"""3D viewport: mouse navigation (turntable orbit, pan, zoom), floor grid and axes."""

from __future__ import annotations

import math
from typing import Any, Protocol

import pyvista as pv
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QWidget
from pyvistaqt import QtInteractor
from vtkmodules.vtkCommonCore import vtkCommand
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleUser
from vtkmodules.vtkRenderingCore import vtkPropPicker, vtkRenderer

from simpledesign.ui.camera import STANDARD_VIEWS, Vec3, scale_about, turntable

WHEEL_ZOOM_FACTOR = 1.15

GRID_SIZE_MM = 4000.0
GRID_STEP_MM = 100.0
AXIS_LENGTH_MM = 300.0

# Camera framing when the scene is still empty: a typical piece of furniture.
EMPTY_SCENE_BOUNDS = (0.0, 1000.0, 0.0, 500.0, 0.0, 1000.0)

ORBIT_DEGREES_PER_PX = 0.4
DRAG_TOLERANCE_PX = 3


def world_per_pixel(renderer: vtkRenderer, point: Vec3) -> float:
    """Size of one screen pixel in world units at ``point``."""
    camera = renderer.GetActiveCamera()
    height = max(renderer.GetSize()[1], 1)
    if camera.GetParallelProjection():
        return float(2.0 * camera.GetParallelScale() / height)
    position = camera.GetPosition()
    direction = camera.GetDirectionOfProjection()
    depth = sum((point[i] - position[i]) * direction[i] for i in range(3))
    return 2.0 * max(depth, 1e-6) * math.tan(math.radians(camera.GetViewAngle() / 2)) / height


class PointerHandler(Protocol):
    """Receives the mouse events that are not camera navigation."""

    def press(self, x: int, y: int, ctrl: bool) -> bool:
        """Left button down. True if the handler takes the drag (gizmo, part);
        otherwise a drag orbits the camera."""
        ...

    def move(self, x: int, y: int) -> bool:
        """Mouse moved with the left button down. True if consumed."""
        ...

    def release(self, x: int, y: int, ctrl: bool) -> None: ...

    def hover(self, x: int, y: int) -> None:
        """Mouse moved without a button."""
        ...

    def context_menu(self, x: int, y: int) -> None:
        """Right click without moving."""
        ...

    def cancel(self) -> None:
        """Escape: abort a running drag."""
        ...


_RELEASE_BUTTON = {
    "LeftButtonReleaseEvent": "left",
    "MiddleButtonReleaseEvent": "middle",
    "RightButtonReleaseEvent": "right",
}


class FusionInteractorStyle(vtkInteractorStyleUser):
    """Mouse mapping:

    * left drag on empty space   → orbit (turntable, Z stays up) about the point
      under the cursor
    * left click / drag on a part or gizmo arrow → ``pointer_handler`` (select, move)
    * middle drag                → pan, Shift + middle drag → orbit
    * right drag                 → pan, right click → context menu
    * mouse wheel                → zoom towards the point under the cursor

    Observers registered on the style *replace* VTK's default handlers.
    """

    def __init__(self) -> None:
        super().__init__()
        self._picker = vtkPropPicker()
        self.pointer_handler: PointerHandler | None = None
        self._button: str | None = None  # "left" / "middle" / "right"
        self._mode: str | None = None  # "handler" / "orbit" / "pan" / None (undecided)
        self._press: tuple[int, int] = (0, 0)
        self._last: tuple[int, int] = (0, 0)
        self._pivot: Vec3 = (0.0, 0.0, 0.0)
        for event, handler in (
            (vtkCommand.LeftButtonPressEvent, self._on_left_press),
            (vtkCommand.LeftButtonReleaseEvent, self._on_release),
            (vtkCommand.MiddleButtonPressEvent, self._on_middle_press),
            (vtkCommand.MiddleButtonReleaseEvent, self._on_release),
            (vtkCommand.RightButtonPressEvent, self._on_right_press),
            (vtkCommand.RightButtonReleaseEvent, self._on_release),
            (vtkCommand.MouseMoveEvent, self._on_mouse_move),
            (vtkCommand.MouseWheelForwardEvent, self._on_wheel_forward),
            (vtkCommand.MouseWheelBackwardEvent, self._on_wheel_backward),
            # VTK's built-in key bindings ("q"/"e" quit, "w" wireframe, …) must not fire.
            (vtkCommand.CharEvent, self._ignore),
            (vtkCommand.KeyPressEvent, self._ignore),
        ):
            self.AddObserver(event, handler)

    # ------------------------------------------------------------------ helpers
    def _ignore(self, _obj: Any, _event: str) -> None:
        return

    def _position(self) -> tuple[int, int]:
        x, y = self.GetInteractor().GetEventPosition()
        return int(x), int(y)

    def _renderer(self) -> vtkRenderer | None:
        renderer: vtkRenderer | None = self.GetDefaultRenderer()
        if renderer is None:
            x, y = self._position()
            self.FindPokedRenderer(x, y)
            renderer = self.GetCurrentRenderer()
        return renderer

    def _start(self, button: str, mode: str | None) -> None:
        self._button = button
        self._mode = mode
        self._press = self._last = self._position()
        if mode == "orbit":
            self._pivot = self._pick_pivot(*self._press)

    def _pick_pivot(self, x: int, y: int) -> Vec3:
        renderer = self._renderer()
        if renderer is None:
            return (0.0, 0.0, 0.0)
        if self._picker.Pick(x, y, 0, renderer):
            px, py, pz = self._picker.GetPickPosition()
            return (px, py, pz)
        fx, fy, fz = renderer.GetActiveCamera().GetFocalPoint()
        return (fx, fy, fz)

    # ------------------------------------------------------------------ buttons
    def _on_left_press(self, _obj: Any, _event: str) -> None:
        if self._button is not None:
            return
        x, y = self._position()
        ctrl = bool(self.GetInteractor().GetControlKey())
        owned = self.pointer_handler is not None and self.pointer_handler.press(x, y, ctrl)
        self._start("left", "handler" if owned else None)

    def _on_middle_press(self, _obj: Any, _event: str) -> None:
        if self._button is None:
            self._start("middle", "orbit" if self.GetInteractor().GetShiftKey() else "pan")

    def _on_right_press(self, _obj: Any, _event: str) -> None:
        if self._button is None:
            self._start("right", None)

    def _on_release(self, _obj: Any, event: str) -> None:
        button = _RELEASE_BUTTON.get(event)
        if button is None or button != self._button:
            return
        x, y = self._position()
        mode = self._mode
        self._button = self._mode = None
        handler = self.pointer_handler
        if handler is None:
            return
        if button == "left" and mode in ("handler", None):
            handler.release(x, y, bool(self.GetInteractor().GetControlKey()))
        elif button == "right" and mode is None:
            handler.context_menu(x, y)

    def _on_mouse_move(self, _obj: Any, _event: str) -> None:
        x, y = self._position()
        handler = self.pointer_handler
        if self._button is None:
            if handler is not None:
                handler.hover(x, y)
            return
        if self._mode == "handler":
            if handler is not None:
                handler.move(x, y)
            return
        if self._mode is None:
            if max(abs(x - self._press[0]), abs(y - self._press[1])) <= DRAG_TOLERANCE_PX:
                return
            self._mode = "orbit" if self._button == "left" else "pan"
            if self._mode == "orbit":
                self._pivot = self._pick_pivot(*self._press)
        dx, dy = x - self._last[0], y - self._last[1]
        self._last = (x, y)
        if self._mode == "orbit":
            self.orbit(dx, dy)
        elif self._mode == "pan":
            self.pan(dx, dy)

    def cancel(self) -> None:
        """Escape: end any navigation and abort the handler's drag."""
        self._button = self._mode = None
        if self.pointer_handler is not None:
            self.pointer_handler.cancel()

    # ------------------------------------------------------------------ camera
    def set_pivot(self, pivot: Vec3) -> None:
        self._pivot = pivot

    def orbit(self, dx: float, dy: float) -> None:
        """Turntable orbit about the current pivot by a mouse movement in pixels."""
        renderer = self._renderer()
        if renderer is None:
            return
        camera = renderer.GetActiveCamera()
        position, focal, up = turntable(
            camera.GetPosition(),
            camera.GetFocalPoint(),
            camera.GetViewUp(),
            self._pivot,
            -dx * ORBIT_DEGREES_PER_PX,
            -dy * ORBIT_DEGREES_PER_PX,
        )
        camera.SetPosition(*position)
        camera.SetFocalPoint(*focal)
        camera.SetViewUp(*up)
        camera.OrthogonalizeViewUp()
        self._update(renderer)

    def pan(self, dx: float, dy: float) -> None:
        """Move the view so the scene follows the mouse."""
        renderer = self._renderer()
        if renderer is None:
            return
        camera = renderer.GetActiveCamera()
        focal = camera.GetFocalPoint()
        scale = world_per_pixel(renderer, focal)
        up = camera.GetViewUp()
        direction = camera.GetDirectionOfProjection()
        right = (
            direction[1] * up[2] - direction[2] * up[1],
            direction[2] * up[0] - direction[0] * up[2],
            direction[0] * up[1] - direction[1] * up[0],
        )
        shift = [-(dx * right[i] + dy * up[i]) * scale for i in range(3)]
        position = camera.GetPosition()
        camera.SetFocalPoint(focal[0] + shift[0], focal[1] + shift[1], focal[2] + shift[2])
        camera.SetPosition(position[0] + shift[0], position[1] + shift[1], position[2] + shift[2])
        self._update(renderer)

    def _update(self, renderer: vtkRenderer) -> None:
        renderer.ResetCameraClippingRange()
        self.GetInteractor().Render()

    # ------------------------------------------------------------------ zoom
    def _on_wheel_forward(self, _obj: Any, _event: str) -> None:
        self.zoom_to_cursor(WHEEL_ZOOM_FACTOR)

    def _on_wheel_backward(self, _obj: Any, _event: str) -> None:
        self.zoom_to_cursor(1.0 / WHEEL_ZOOM_FACTOR)

    def zoom_to_cursor(self, factor: float) -> None:
        renderer = self._renderer()
        if renderer is None:
            return
        interactor = self.GetInteractor()
        camera = renderer.GetActiveCamera()
        x, y = interactor.GetEventPosition()

        # Prefer the surface under the cursor; otherwise use the focal-plane depth.
        target: Vec3
        if self._picker.Pick(x, y, 0, renderer):
            px, py, pz = self._picker.GetPickPosition()
            target = (px, py, pz)
        else:
            renderer.SetWorldPoint(*camera.GetFocalPoint(), 1.0)
            renderer.WorldToDisplay()
            depth = renderer.GetDisplayPoint()[2]
            renderer.SetDisplayPoint(x, y, depth)
            renderer.DisplayToWorld()
            wx, wy, wz, w = renderer.GetWorldPoint()
            target = (wx / w, wy / w, wz / w)

        camera.SetPosition(*scale_about(camera.GetPosition(), target, factor))
        camera.SetFocalPoint(*scale_about(camera.GetFocalPoint(), target, factor))
        if camera.GetParallelProjection():
            camera.SetParallelScale(camera.GetParallelScale() / factor)
        self._update(renderer)


class Viewport(QtInteractor):
    """The central 3D view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, auto_update=False, multi_samples=8)
        # Not named `style`: that would shadow QWidget.style().
        self.interaction_style = FusionInteractorStyle()
        self.iren.interactor.SetInteractorStyle(self.interaction_style)
        self.interaction_style.SetDefaultRenderer(self.renderer)

        # pyvista is deprecating positional arguments → always pass keywords.
        # The ignores work around pyvista's functools.wraps typing, not our code.
        self.set_background(color="#dfe4ea", top="#ffffff")  # type: ignore[call-arg]
        self.enable_anti_aliasing(aa_type="ssaa")
        self._add_grid()
        self._add_origin_axes()
        self.add_axes(interactive=False, line_width=2, labels_off=False)  # type: ignore[call-arg]
        self.show_view("iso")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.interaction_style.cancel()
            event.accept()
            return
        super().keyPressEvent(event)  # type: ignore[no-untyped-call]

    # ------------------------------------------------------------------ scene helpers
    def _add_grid(self) -> None:
        cells = int(GRID_SIZE_MM / GRID_STEP_MM)
        plane = pv.Plane(
            center=(0, 0, 0),
            direction=(0, 0, 1),
            i_size=GRID_SIZE_MM,
            j_size=GRID_SIZE_MM,
            i_resolution=cells,
            j_resolution=cells,
        )
        actor = self.add_mesh(
            plane,
            style="wireframe",
            color="#9aa5b1",
            opacity=0.35,
            pickable=False,
            name="grid",
            reset_camera=False,
        )
        # The grid must not influence "fit view".
        actor.SetUseBounds(False)

    def _add_origin_axes(self) -> None:
        for name, end, color in (
            ("axis_x", (AXIS_LENGTH_MM, 0, 0), "#d64545"),
            ("axis_y", (0, AXIS_LENGTH_MM, 0), "#3fa34d"),
            ("axis_z", (0, 0, AXIS_LENGTH_MM), "#3b6fd6"),
        ):
            actor = self.add_mesh(
                pv.Line(pointa=(0, 0, 0), pointb=end),
                color=color,
                line_width=3,
                pickable=False,
                name=name,
                reset_camera=False,
            )
            actor.SetUseBounds(False)

    # ------------------------------------------------------------------ camera
    def show_view(self, name: str) -> None:
        """Switch to a standard view (``front``, ``top``, ``right``, ``iso`` …) and fit."""
        view = STANDARD_VIEWS[name]
        camera = self.renderer.GetActiveCamera()
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetPosition(*view.direction)
        camera.SetViewUp(*view.up)
        self.fit_view()

    def fit_view(self) -> None:
        """Zoom so that all parts are visible (or a default box if empty)."""
        bounds = self.renderer.ComputeVisiblePropBounds()
        if bounds[0] > bounds[1]:  # VTK signals "no visible props" with min > max
            self.renderer.ResetCamera(EMPTY_SCENE_BOUNDS)
        else:
            self.renderer.ResetCamera(bounds)
        self.render()
