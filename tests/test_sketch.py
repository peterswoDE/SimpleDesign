"""Phase 5: sketch model, own solver and profile detection."""

from __future__ import annotations

import math

import pytest

from simpledesign.core.sketch import model, profile
from simpledesign.core.sketch.model import ConstraintKind as K
from simpledesign.core.sketch.model import Sketch
from simpledesign.core.sketch.solver import NumpySolver, References, Status

REFS = References.for_plane()


def rect(sketch: Sketch, w: float = 100, h: float = 50) -> list[model.Line]:
    lines = sketch.add_rectangle((3, 4), (3 + w * 0.9, 4 + h * 1.1))
    return lines


def solve(sketch: Sketch, values: dict[str, float] | None = None, **kw: object):  # type: ignore[no-untyped-def]
    result = NumpySolver().solve(sketch, values or {}, REFS, **kw)  # type: ignore[arg-type]
    return result


def test_rectangle_fully_determined() -> None:
    s = Sketch()
    lines = rect(s)
    corner = lines[0].start
    s.add_constraint(K.COINCIDENT, (corner, model.ORIGIN))
    width = s.add_constraint(K.DISTANCE, (lines[0].id,))
    height = s.add_constraint(K.DISTANCE, (lines[1].id,))
    result = solve(s, {width.id: 120, height.id: 40})
    assert result.status == Status.SOLVED and result.dof == 0
    result.apply(s)
    regions = profile.regions(s, {p.id: p.pos for p in s.points.values()}, {})
    (lo, hi) = profile.bounds(regions)
    assert lo == pytest.approx((0, 0), abs=1e-6)
    assert hi == pytest.approx((120, 40), abs=1e-6)


def test_underdetermined_keeps_shape_and_counts_dof() -> None:
    s = Sketch()
    rect(s)
    result = solve(s)
    assert result.status == Status.UNDERDETERMINED
    assert result.dof == 4  # x, y, width, height
    before = {p.id: p.pos for p in s.points.values()}
    for pid, pos in result.points.items():
        assert pos == pytest.approx(before[pid])


def test_contradiction_fails_and_keeps_last_state() -> None:
    s = Sketch()
    lines = rect(s)
    a = s.add_constraint(K.DISTANCE, (lines[0].id,))
    b = s.add_constraint(K.DISTANCE, (lines[2].id,))
    result = solve(s, {a.id: 100, b.id: 60})  # opposite sides of a rectangle
    assert result.status == Status.FAILED
    assert result.failed
    before = {p.id: p.pos for p in s.points.values()}
    assert result.points == before


def test_redundant() -> None:
    s = Sketch()
    lines = rect(s)
    s.add_constraint(K.PARALLEL, (lines[0].id, lines[2].id))  # already implied
    s.add_constraint(K.FIX, (lines[0].start,), (3.0, 4.0))
    a = s.add_constraint(K.DISTANCE, (lines[0].id,))
    b = s.add_constraint(K.DISTANCE, (lines[1].id,))
    result = solve(s, {a.id: 10, b.id: 20})
    assert result.status == Status.REDUNDANT


def test_circle_tangent_and_radius() -> None:
    s = Sketch()
    lines = rect(s)
    s.add_constraint(K.COINCIDENT, (lines[0].start, model.ORIGIN))
    w = s.add_constraint(K.DISTANCE, (lines[0].id,))
    h = s.add_constraint(K.DISTANCE, (lines[1].id,))
    center = s.add_point(50, 20)
    circle = s.add_circle(center.id, 8)
    r = s.add_constraint(K.RADIUS, (circle.id,))
    s.add_constraint(K.TANGENT, (lines[0].id, circle.id))
    s.add_constraint(K.TANGENT, (lines[1].id, circle.id))
    result = solve(s, {w.id: 100, h.id: 50, r.id: 10})
    assert result.status == Status.SOLVED
    assert result.points[center.id] == pytest.approx((90, 10), abs=1e-6)
    assert result.radii[circle.id] == pytest.approx(10)
    result.apply(s)
    regions = profile.regions(s, {p.id: p.pos for p in s.points.values()}, result.radii)
    assert len(regions) == 1 and len(regions[0].holes) == 1


def test_angle_and_hdistance() -> None:
    s = Sketch()
    a = s.add_point(0, 0)
    b = s.add_point(50, 10)
    line = s.add_line(a.id, b.id)
    s.add_constraint(K.COINCIDENT, (a.id, model.ORIGIN))
    angle = s.add_constraint(K.ANGLE, ("ox", line.id))
    hd = s.add_constraint(K.HDISTANCE, (a.id, b.id))
    result = solve(s, {angle.id: 30, hd.id: 60})
    assert result.status == Status.SOLVED
    assert result.points[b.id] == pytest.approx((60, 60 * math.tan(math.radians(30))), abs=1e-6)


def test_drag_moves_free_point() -> None:
    s = Sketch()
    lines = rect(s)
    s.add_constraint(K.COINCIDENT, (lines[0].start, model.ORIGIN))
    corner = lines[1].end  # opposite corner
    result = solve(s, drag={corner: (200.0, 80.0)})
    assert result.ok
    assert result.points[corner] == pytest.approx((200, 80), abs=1e-5)
    # still a rectangle
    p = result.points
    assert p[lines[0].end][1] == pytest.approx(0, abs=1e-6)


def test_arc_profile_and_bounds() -> None:
    s = Sketch()
    a, b, c = s.add_point(0, 0), s.add_point(100, 0), s.add_point(0, 0)
    center = s.add_point(50, 0)
    s.add_line(a.id, b.id)
    s.add_arc(center.id, b.id, c.id)  # upper half circle back to a
    s.add_constraint(K.COINCIDENT, (a.id, c.id))
    result = solve(s)
    assert result.ok
    regions = profile.regions(s, {p.id: p.pos for p in s.points.values()}, {})
    lo, hi = profile.bounds(regions)
    assert lo == pytest.approx((0, 0), abs=1e-6)
    assert hi == pytest.approx((100, 50), abs=1e-6)


def test_open_and_branched_profiles() -> None:
    s = Sketch()
    a, b, c = s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 10)
    s.add_line(a.id, b.id)
    s.add_line(b.id, c.id)
    positions = {p.id: p.pos for p in s.points.values()}
    with pytest.raises(profile.ProfileError) as info:
        profile.regions(s, positions, {})
    assert info.value.problem == profile.ProfileProblem.OPEN
    with pytest.raises(profile.ProfileError):
        profile.regions(Sketch(), {}, {})
    s.add_line(c.id, a.id)
    s.add_line(b.id, s.add_point(20, 0).id, construction=True)  # construction is ignored
    assert len(profile.regions(s, {p.id: p.pos for p in s.points.values()}, {})) == 1
    d = s.add_point(20, 20)
    s.add_line(b.id, d.id)
    s.add_line(d.id, c.id)
    with pytest.raises(profile.ProfileError) as info:
        profile.regions(s, {p.id: p.pos for p in s.points.values()}, {})
    assert info.value.problem == profile.ProfileProblem.BRANCHED


def test_face_references() -> None:
    refs = References.for_face(400, 200)
    s = Sketch()
    center = s.add_point(10, 10)
    circle = s.add_circle(center.id, 5)
    dx = s.add_constraint(K.DISTANCE, (center.id, "e1"))  # to the right edge
    dy = s.add_constraint(K.VDISTANCE, ("r1", center.id))
    s.add_constraint(K.RADIUS, (circle.id,))
    values = {dx.id: 30, dy.id: 50}
    values[next(c.id for c in s.constraints.values() if c.kind == K.RADIUS)] = 4
    result = NumpySolver().solve(s, values, refs)
    assert result.status == Status.SOLVED
    assert result.points[center.id] == pytest.approx((370, 50), abs=1e-6)


def test_delete_cascades_and_json_round_trip() -> None:
    s = Sketch()
    lines = rect(s)
    d = s.add_constraint(K.DISTANCE, (lines[0].id,))
    data = model.to_dict(s)
    again = model.from_dict(data)
    assert model.to_dict(again) == data
    s.delete({lines[0].id})
    assert d.id not in s.constraints
    assert lines[0].id not in s.entities
    assert len(s.points) == 4  # corners still used by the other lines
    s.delete({lines[1].start})
    assert len(s.entities) == 2
    with pytest.raises(ValueError):
        model.from_dict(
            {
                "points": [],
                "entities": [{"type": "line", "id": "x", "points": ["a", "b"]}],
                "constraints": [],
            }
        )
