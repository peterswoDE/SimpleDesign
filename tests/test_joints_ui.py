"""Joints in the controller (undo) and in the UI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.model.joint import JointKind
from simpledesign.ui.main_window import MainWindow
from tests.furniture import board_id, build_korpus


@pytest.fixture
def controller(tmp_path: Path) -> Controller:
    ctrl = Controller(recovery_dir=tmp_path / "recovery", library_path=tmp_path / "lib.json")
    path = tmp_path / "korpus.sdz"
    sdz.save(build_korpus(), path)
    ctrl.open(path)
    return ctrl


@pytest.fixture
def window(qtbot: QtBot, controller: Controller) -> Iterator[MainWindow]:
    win = MainWindow(controller)
    win.show()
    qtbot.waitExposed(win)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def side_and_bottom(controller: Controller) -> tuple[str, str]:
    return board_id(controller.doc, "Seite_links"), board_id(controller.doc, "Boden")


def test_joint_changes_are_undoable(controller: Controller) -> None:
    side, bottom = side_and_bottom(controller)
    joint = controller.add_joint(JointKind.DOWEL, side, bottom)
    assert controller.evaluation.machining.hardware[0].quantity == 3
    controller.set_joint_param(joint, "count", 4)
    controller.set_joint_param(joint, "count", 5)  # merged with the step before
    assert controller.evaluation.machining.hardware[0].quantity == 5
    controller.swap_joint(joint)
    assert controller.doc.joints[joint].main_id == bottom
    controller.undo_stack.undo()
    controller.undo_stack.undo()
    assert controller.doc.joints[joint].param("count") == 0
    controller.delete_joints([joint])
    assert not controller.doc.joints
    controller.undo_stack.undo()
    controller.undo_stack.undo()
    assert not controller.doc.joints


def test_menu_creates_joint_from_selection(window: MainWindow) -> None:
    controller = window.controller
    panel = window.joints_panel
    action = panel.add_actions[JointKind.GROOVE]
    side, bottom = side_and_bottom(controller)
    controller.set_selection([side])
    assert not action.isEnabled()
    controller.set_selection([side, bottom])
    assert action.isEnabled()
    action.trigger()
    (joint,) = controller.doc.joints.values()
    assert (joint.kind, joint.main_id, joint.attached_id) == (JointKind.GROOVE, side, bottom)
    assert panel.current_id() == joint.id
    assert not panel.details.isHidden()
    assert window.cutlist_panel.table.rowCount() == 6  # left side and bottom split off


def test_panel_edits_settings_and_shows_errors(window: MainWindow) -> None:
    controller = window.controller
    panel = window.joints_panel
    side, bottom = side_and_bottom(controller)
    controller.set_selection([side, bottom])
    panel.add_actions[JointKind.DOWEL].trigger()
    assert panel.spins["depth"].isHidden()
    assert not panel.spins["depth_main"].isHidden()
    panel.spins["depth_main"].setValue(20)
    joint = panel.current_id()
    assert joint is not None
    assert controller.doc.joints[joint].param("depth_main") == 20
    assert "tief" in panel.error_label.text()
    assert panel.list.item(0).text().startswith("⚠")
    assert window.cutlist_panel.hardware_table.isHidden()

    panel.spins["depth_main"].setValue(12)
    assert panel.error_label.isHidden()
    assert not window.cutlist_panel.hardware_table.isHidden()
    fitting = window.cutlist_panel.hardware_table.item(0, 1)
    assert fitting is not None
    assert fitting.text() == "Dübel 8 × 40 mm"

    panel.swap_button.click()
    assert controller.doc.joints[joint].main_id == bottom
    panel.delete_button.click()
    assert not controller.doc.joints
    assert panel.details.isHidden()
