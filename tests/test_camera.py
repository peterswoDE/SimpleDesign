from __future__ import annotations

import math

import pytest

from simpledesign.ui.camera import STANDARD_VIEWS, scale_about


def test_scale_about_keeps_center_fixed() -> None:
    center = (10.0, 20.0, 30.0)
    assert scale_about(center, center, 2.0) == center


def test_scale_about_halves_distance_for_factor_two() -> None:
    camera = (0.0, -1000.0, 0.0)
    target = (0.0, 0.0, 0.0)
    assert scale_about(camera, target, 2.0) == (0.0, -500.0, 0.0)


def test_scale_about_keeps_view_direction() -> None:
    """Camera position and focal point move together → direction unchanged."""
    position, focal, target = (100.0, -800.0, 400.0), (0.0, 0.0, 0.0), (50.0, 0.0, 300.0)
    new_position = scale_about(position, target, 1.15)
    new_focal = scale_about(focal, target, 1.15)

    def direction(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
        d = [bi - ai for ai, bi in zip(a, b, strict=True)]
        n = math.hypot(*d)
        return tuple(x / n for x in d)

    assert direction(position, focal) == pytest.approx(direction(new_position, new_focal))


def test_scale_about_rejects_non_positive_factor() -> None:
    with pytest.raises(ValueError):
        scale_about((1.0, 1.0, 1.0), (0.0, 0.0, 0.0), 0.0)


def test_standard_views_have_z_up_except_top() -> None:
    for name, view in STANDARD_VIEWS.items():
        if name != "top":
            assert view.up == (0.0, 0.0, 1.0)
    assert STANDARD_VIEWS["front"].direction == (0.0, -1.0, 0.0)
