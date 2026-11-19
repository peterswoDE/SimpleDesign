"""Section view (Schnittansicht): clipping plane and hatched cut faces."""

from __future__ import annotations

import os

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.geometry.mesh import Mesh
from simpledesign.core.geometry.placement import world_bounds
from simpledesign.core.geometry.section import SectionPlane, section_cap
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.joint import JointKind
from simpledesign.core.model.machining import PartShape
from simpledesign.ui.main_window import MainWindow

from .furniture import board_id, build_korpus


def _area(mesh: Mesh) -> float:
    faces = mesh.faces.reshape(-1, 4)[:, 1:]
    p = mesh.points
    cross = np.cross(p[faces[:, 1]] - p[faces[:, 0]], p[faces[:, 2]] - p[faces[:, 0]])
    return float(0.5 * np.linalg.norm(cross, axis=1).sum())


def test_plane_sides() -> None:
    plane = SectionPlane(0, 100.0)
    assert plane.normal == (-1.0, 0.0, 0.0) and plane.origin == (100.0, 0.0, 0.0)
    assert plane.keeps((0, 0, 0), (50, 1, 1)) and not plane.keeps((150, 0, 0), (200, 1, 1))
    flipped = SectionPlane(0, 100.0, flip=True)
    assert flipped.normal == (1.0, 0.0, 0.0) and flipped.keeps((150, 0, 0), (200, 1, 1))


def test_cap_of_a_plain_board() -> None:
    shape = PartShape((10.0, 20.0, 30.0), (400.0, 18.0, 600.0))
    cap = section_cap(shape, SectionPlane(2, 330.0))
    assert cap is not None
    assert cap.points[:, 2] == pytest.approx(330.0)
    assert _area(cap) == pytest.approx(400 * 18)
    assert section_cap(shape, SectionPlane(2, 700.0)) is None  # misses the part
    # moved for the exploded view: the cap moves along
    moved = section_cap(shape, SectionPlane(2, 430.0), offset=(0.0, 0.0, 100.0))
    assert moved is not None and _area(moved) == pytest.approx(400 * 18)


def test_cap_shows_holes_and_turned_parts() -> None:
    doc = build_korpus()
    side = board_id(doc, "Seite_links")
    doc.add_joint(JointKind.DOWEL, side, board_id(doc, "Boden"))
    evaluation = Evaluator().evaluate(doc)
    (shape,) = evaluation.machining.parts[side]
    lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
    full = (hi[1] - lo[1]) * (hi[2] - lo[2])
    # cut through the middle of the side: the dowel holes leave gaps
    cap = section_cap(shape, SectionPlane(0, (lo[0] + hi[0]) / 2))
    assert cap is not None and 0.9 * full < _area(cap) < full - 1
    # a turned board (90° about Z) is cut as placed
    turned = PartShape((0.0, 0.0, 0.0), (400.0, 18.0, 600.0), rotation=(2, 90.0))
    t_lo, t_hi = world_bounds(turned.origin, turned.size, turned.rotation)
    cap = section_cap(turned, SectionPlane(2, 300.0))
    assert cap is not None and _area(cap) == pytest.approx(400 * 18, rel=1e-3)
    assert cap.points[:, 0].min() == pytest.approx(t_lo[0], abs=1e-3)
    assert cap.points[:, 1].max() == pytest.approx(t_hi[1], abs=1e-3)


def test_section_view_in_the_window(qtbot: QtBot) -> None:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    try:
        doc = build_korpus()
        win.controller._replace_document(doc, None)
        scene = win.scene
        right = board_id(doc, "Seite_rechts")
        (shape,) = win.controller.evaluation.machining.parts[right]
        lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
        outside = scene.to_display((hi[0], (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
        x, y = round(outside[0]), round(outside[1])
        assert scene.board_at(x, y) == right

        win.action_section.trigger()
        assert scene.section is not None and win.section_box.isVisibleTo(win)
        assert scene.section.axis == 0
        ((face, edge),) = scene._board_actors[right]
        assert face.GetMapper().GetNumberOfClippingPlanes() == 1
        assert edge.GetMapper().GetNumberOfClippingPlanes() == 1
        assert scene._cap_actor.GetVisibility()
        caps = scene._cap_mapper.GetInput()
        assert caps.GetNumberOfCells() > 0
        assert caps.GetPointData().GetTCoords() is not None
        # the right side lies beyond the cut: it is hidden and not picked
        assert scene.board_at(x, y) != right

        # the camera looks from +x: the cut faces it, the far half stays
        assert not scene.section.flip
        win.section_flip.setChecked(True)
        assert scene.section.flip and scene.board_at(x, y) == right
        win.section_axis.setCurrentIndex(2)
        assert scene.section.axis == 2
        assert win.section_slider.minimum() <= scene.section.position
        win.section_slider.setValue(win.section_slider.minimum() + 300)
        assert scene.section.position == win.section_slider.minimum() + 300
        if os.environ.get("SD_SHOT"):
            win.viewport.show_view("iso")
            win.section_axis.setCurrentIndex(1)
            _shot(win, os.environ["SD_SHOT"])

        win.action_section.trigger()
        assert scene.section is None and not win.section_box.isVisibleTo(win)
        assert face.GetMapper().GetNumberOfClippingPlanes() == 0
        assert not scene._cap_actor.GetVisibility()
    finally:
        win.controller.undo_stack.setClean()
        win.close()
        win.deleteLater()


def _shot(win: MainWindow, path: str) -> None:  # pragma: no cover - manual check
    from vtkmodules.vtkIOImage import vtkPNGWriter
    from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter

    win.viewport.render()
    grab = vtkWindowToImageFilter()
    grab.SetInput(win.viewport.ren_win)
    grab.ReadFrontBufferOff()
    grab.Update()
    writer = vtkPNGWriter()
    writer.SetFileName(path)
    writer.SetInputConnection(grab.GetOutputPort())
    writer.Write()
