"""3D viewport: Fusion-like mouse navigation, floor grid and axes."""

from __future__ import annotations

from typing import Any, Protocol

import pyvista as pv
from PySide6.QtWidgets import QWidget
from pyvistaqt import QtInteractor
from vtkmodules.vtkCommonCore import vtkCommand
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingCore import vtkPropPicker

from simpledesign.ui.camera import STANDARD_VIEWS, Vec3, scale_about

WHEEL_ZOOM_FACTOR = 1.15

GRID_SIZE_MM = 4000.0
GRID_STEP_MM = 100.0
AXIS_LENGTH_MM = 300.0

# Camera framing when the scene is still empty: a typical piece of furniture.
EMPTY_SCENE_BOUNDS = (0.0, 1000.0, 0.0, 500.0, 0.0, 1000.0)

# vtkInteractorStyle states
_STATE_ROTATE = 1
_STATE_PAN = 2


class PointerHandler(Protocol):
    """Receives left-button events (selection, gizmo drag) from the style."""

    def press(self, x: int, y: int, ctrl: bool) -> None: ...

    def move(self, x: int, y: int) -> bool:
        """Return True if the move was consumed (e.g. a drag is running)."""
        ...

    def release(self, x: int, y: int, ctrl: bool) -> None: ...


class FusionInteractorStyle(vtkInteractorStyleTrackballCamera):
    """Mouse mapping like Fusion 360.

    * middle button drag         → pan
    * Shift + middle button drag → orbit
    * mouse wheel                → zoom towards the point under the cursor
    * left button                → forwarded to ``pointer_handler`` (select, drag)
    * right button               → reserved for context menus

    Observers registered on the style *replace* VTK's default handlers.
    """

    def __init__(self) -> None:
        super().__init__()
        self._picker = vtkPropPicker()
        self.pointer_handler: PointerHandler | None = None
        self.AddObserver(vtkCommand.LeftButtonPressEvent, self._on_left_press)
        self.AddObserver(vtkCommand.LeftButtonReleaseEvent, self._on_left_release)
        self.AddObserver(vtkCommand.MouseMoveEvent, self._on_mouse_move)
        self.AddObserver(vtkCommand.MiddleButtonPressEvent, self._on_middle_press)
        self.AddObserver(vtkCommand.MiddleButtonReleaseEvent, self._on_middle_release)
        self.AddObserver(vtkCommand.MouseWheelForwardEvent, self._on_wheel_forward)
        self.AddObserver(vtkCommand.MouseWheelBackwardEvent, self._on_wheel_backward)
        for event in (
            vtkCommand.RightButtonPressEvent,
            vtkCommand.RightButtonReleaseEvent,
            # VTK's built-in key bindings ("q"/"e" quit, "w" wireframe, …) must not fire.
            vtkCommand.CharEvent,
        ):
            self.AddObserver(event, self._ignore)

    # ------------------------------------------------------------------ buttons
    def _ignore(self, _obj: Any, _event: str) -> None:
        return

    def _event(self) -> tuple[int, int, bool]:
        interactor = self.GetInteractor()
        x, y = interactor.GetEventPosition()
        return x, y, bool(interactor.GetControlKey())

    def _on_left_press(self, _obj: Any, _event: str) -> None:
        if self.pointer_handler is not None:
            self.pointer_handler.press(*self._event())

    def _on_left_release(self, _obj: Any, _event: str) -> None:
        if self.pointer_handler is not None:
            self.pointer_handler.release(*self._event())

    def _on_mouse_move(self, _obj: Any, _event: str) -> None:
        x, y, _ctrl = self._event()
        if self.pointer_handler is not None and self.pointer_handler.move(x, y):
            return
        self.OnMouseMove()  # default: continue pan / orbit

    def _poke(self) -> bool:
        x, y = self.GetInteractor().GetEventPosition()
        self.FindPokedRenderer(x, y)
        return self.GetCurrentRenderer() is not None

    def _on_middle_press(self, _obj: Any, _event: str) -> None:
        if not self._poke():
            return
        if self.GetInteractor().GetShiftKey():
            self.StartRotate()
        else:
            self.StartPan()

    def _on_middle_release(self, _obj: Any, _event: str) -> None:
        state = self.GetState()
        if state == _STATE_ROTATE:
            self.EndRotate()
        elif state == _STATE_PAN:
            self.EndPan()

    # ------------------------------------------------------------------ zoom
    def _on_wheel_forward(self, _obj: Any, _event: str) -> None:
        self.zoom_to_cursor(WHEEL_ZOOM_FACTOR)

    def _on_wheel_backward(self, _obj: Any, _event: str) -> None:
        self.zoom_to_cursor(1.0 / WHEEL_ZOOM_FACTOR)

    def zoom_to_cursor(self, factor: float) -> None:
        if not self._poke():
            return
        interactor = self.GetInteractor()
        renderer = self.GetCurrentRenderer()
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
        renderer.ResetCameraClippingRange()
        interactor.Render()


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
