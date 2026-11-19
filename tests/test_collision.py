"""Collision check: overlapping parts, joints do not count."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from simpledesign.core.geometry.collision import Collision, find_collisions
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.machining import PartShape
from simpledesign.core.templates.builtin import builtin_templates
from simpledesign.ui.main_window import MainWindow

from .furniture import board_id, build_korpus


def _collisions(doc: Document) -> list[Collision]:
    evaluation = Evaluator().evaluate(doc)
    return find_collisions(evaluation.machining.parts)


def test_touching_parts_do_not_collide() -> None:
    assert _collisions(build_korpus()) == []


def test_shelves_reaching_into_the_side_collide() -> None:
    doc = build_korpus()
    shelf = board_id(doc, "Fachboden")
    doc.set_expression(shelf, "x", "0")
    found = _collisions(doc)
    side = board_id(doc, "Seite_links")
    assert len(found) == 4  # every copy of the pattern
    for hit in found:
        assert {hit.first, hit.second} == {side, shelf}
        assert hit.volume == pytest.approx(18 * 380 * 18)
        assert hit.hi[0] - hit.lo[0] == pytest.approx(18)


def test_rotated_parts_use_the_solid_intersection() -> None:
    a = PartShape((0.0, 0.0, 0.0), (100.0, 100.0, 100.0))
    b = PartShape((50.0, 0.0, 0.0), (100.0, 100.0, 100.0), rotation=(2, 0.0))
    (hit,) = find_collisions({"a": [a], "b": [b]})
    assert hit.volume == pytest.approx(50 * 100 * 100, rel=1e-6)
    assert hit.lo[0] == pytest.approx(50, abs=1e-3)


def test_builtin_templates_are_free_of_collisions() -> None:
    # grooves, rebates and tenons are cut out of the other part
    for template in builtin_templates():
        assert _collisions(template.document()) == [], template.name


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    win = MainWindow()
    win.show()
    qtbot.waitExposed(win)
    win.controller._replace_document(build_korpus(), None)
    yield win
    win.controller.undo_stack.setClean()
    win.close()
    win.deleteLater()


def test_collision_view_shows_overlaps(window: MainWindow) -> None:
    scene = window.scene
    window.action_collisions.trigger()
    assert scene.show_collisions and scene.collisions == []
    assert "Keine" in window.statusBar().currentMessage()
    c = window.controller
    c.set_expression(board_id(c.doc, "Fachboden"), "x", "0")
    assert len(scene.collisions) == 4
    assert scene._collision_actor.GetVisibility()
    message = window.statusBar().currentMessage()
    assert "Seite_links" in message and "Fachboden" in message
    window.action_collisions.trigger()
    assert not scene.show_collisions and scene.collisions == []
    assert not scene._collision_actor.GetVisibility()
