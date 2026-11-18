from __future__ import annotations

import pytest

from simpledesign.app.snapping import SnapTarget, axis_parameter, best_snap, snap_targets
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.part import Axis
from tests.furniture import board_id, build_korpus


def test_axis_parameter_perpendicular_ray() -> None:
    # Axis along X through the origin, ray straight down at x = 250.
    t = axis_parameter((0, 0, 0), (1, 0, 0), (250, 0, 1000), (0, 0, -1))
    assert t == pytest.approx(250)


def test_axis_parameter_parallel_ray_is_zero() -> None:
    assert axis_parameter((0, 0, 0), (1, 0, 0), (0, 5, 5), (1, 0, 0)) == 0


def test_best_snap_picks_closest_within_tolerance() -> None:
    targets = [SnapTarget(0, "0"), SnapTarget(100, "A.x"), SnapTarget(104, "B.x")]
    assert best_snap(targets, 103, 5) == SnapTarget(104, "B.x")
    assert best_snap(targets, 50, 5) is None


def test_snap_targets_of_korpus() -> None:
    doc = build_korpus()
    evaluation = Evaluator().evaluate(doc)
    shelf = board_id(doc, "Fachboden")
    targets = {t.formula: t.value for t in snap_targets(doc, evaluation, shelf, Axis.X)}
    # Shelf start on the inner face of the left side, end on the right side.
    assert targets["Seite_links.x2"] == 18
    assert targets["Seite_rechts.x - Fachboden.length"] == pytest.approx(782 - 764)


def test_snap_targets_include_pattern_copies() -> None:
    doc = build_korpus()
    evaluation = Evaluator().evaluate(doc)
    bottom = board_id(doc, "Boden")
    formulas = {t.formula for t in snap_targets(doc, evaluation, bottom, Axis.Z)}
    assert "Fachboden.z + 3 * Fachboden.spacing" in formulas
