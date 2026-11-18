from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.app.controller import Controller
from simpledesign.core.io import sdz
from simpledesign.core.model.part import Axis
from simpledesign.core.params.errors import FormulaError
from tests.furniture import board_id, build_korpus, param_id


@pytest.fixture
def controller(qtbot: QtBot, tmp_path: Path) -> Controller:
    return Controller(recovery_dir=tmp_path / "recovery")


def korpus_controller(controller: Controller, tmp_path: Path) -> Controller:
    path = tmp_path / "korpus.sdz"
    sdz.save(build_korpus(), path)
    controller.open(path)
    return controller


def test_add_board_is_undoable(controller: Controller) -> None:
    new_id = controller.add_board()
    assert new_id in controller.doc.boards
    assert controller.selection == [new_id]
    assert controller.modified
    controller.undo_stack.undo()
    assert not controller.doc.boards
    assert controller.selection == []
    controller.undo_stack.redo()
    assert new_id in controller.doc.boards


def test_failed_change_leaves_document_untouched(controller: Controller) -> None:
    new_id = controller.add_board()
    before = sdz.to_dict(controller.doc)
    with pytest.raises(FormulaError):
        controller.set_expression(new_id, "length", "3 +* 4")
    assert sdz.to_dict(controller.doc) == before
    assert controller.undo_stack.count() == 1


def test_no_op_change_creates_no_undo_step(controller: Controller) -> None:
    new_id = controller.add_board()
    controller.set_expression(new_id, "length", "600")
    assert controller.undo_stack.count() == 1


def test_parameter_change_updates_evaluation(controller: Controller, tmp_path: Path) -> None:
    c = korpus_controller(controller, tmp_path)
    left = board_id(c.doc, "Seite_links")
    c.set_expression(param_id(c.doc, "H"), None, "2000")
    assert c.evaluation.boards[left].length == 2000
    c.undo_stack.undo()
    assert c.evaluation.boards[left].length == 1800


def test_move_board_falls_back_to_value_on_cycle(controller: Controller, tmp_path: Path) -> None:
    c = korpus_controller(controller, tmp_path)
    left = board_id(c.doc, "Seite_links")
    # Boden.x already depends on Seite_links → this would be a cycle.
    c.move_board(left, Axis.X, "Boden.x", 42)
    assert c.doc.display_expression(left, "x") == "42"


def test_move_board_keeps_snap_formula(controller: Controller, tmp_path: Path) -> None:
    c = korpus_controller(controller, tmp_path)
    shelf = board_id(c.doc, "Fachboden")
    c.move_board(shelf, Axis.Y, "Seite_links.y", 0)
    assert c.doc.display_expression(shelf, "y") == "Seite_links.y"


def test_delete_freezes_references(controller: Controller, tmp_path: Path) -> None:
    c = korpus_controller(controller, tmp_path)
    left = board_id(c.doc, "Seite_links")
    bottom = board_id(c.doc, "Boden")
    c.set_selection([left])
    c.delete([left])
    assert left not in c.doc.boards
    assert c.selection == []
    assert c.doc.display_expression(bottom, "x") == "18"


def test_duplicate_selects_copies(controller: Controller) -> None:
    original = controller.add_board()
    copies = controller.duplicate([original])
    assert len(copies) == 1
    assert controller.selection == copies


def test_save_marks_clean_and_reopens(controller: Controller, tmp_path: Path) -> None:
    controller.add_board()
    path = tmp_path / "test.sdz"
    controller.save(path)
    assert not controller.modified
    assert controller.file_path == path
    controller.new_document()
    assert not controller.doc.boards
    controller.open(path)
    assert len(controller.doc.boards) == 1
    assert not controller.modified


def test_autosave_and_recovery(controller: Controller, tmp_path: Path) -> None:
    controller.autosave()
    assert not controller.recovery_available()  # nothing changed yet
    controller.add_board()
    controller.autosave()
    assert controller.recovery_available()

    other = Controller(recovery_dir=controller.recovery_path.parent)
    assert other.recovery_available()
    other.restore_recovery()
    assert len(other.doc.boards) == 1
    assert other.modified
    other.discard_recovery()
    assert not other.recovery_available()


def test_save_discards_recovery(controller: Controller, tmp_path: Path) -> None:
    controller.add_board()
    controller.autosave()
    controller.save(tmp_path / "x.sdz")
    assert not controller.recovery_available()


def test_group_from_selection(controller: Controller) -> None:
    a = controller.add_board()
    b = controller.add_board()
    group = controller.add_group(None, [a, b])
    assert controller.doc.boards[a].group_id == group
    assert controller.doc.boards[b].group_id == group
    controller.undo_stack.undo()
    assert not controller.doc.groups


def test_visibility(controller: Controller) -> None:
    a = controller.add_board()
    controller.set_visible([a], False)
    assert not controller.doc.boards[a].visible
    controller.undo_stack.undo()
    assert controller.doc.boards[a].visible
