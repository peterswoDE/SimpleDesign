"""Section view (Schnittansicht): a plane across one world axis cuts the
model open. Everything beyond the plane is hidden (done by the renderer);
here the cut faces (caps) are computed so they can be drawn hatched.

A cap is the exact section of the machined part: holes, grooves and
mortises show as gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from simpledesign.core.geometry.mesh import Mesh, make_shape, tessellate
from simpledesign.core.geometry.placement import placed, to_world, world_bounds
from simpledesign.core.model.machining import Feature, PartShape
from simpledesign.core.model.state import Vec3


@dataclass(frozen=True, slots=True)
class SectionPlane:
    """Cut at ``position`` across world ``axis`` (0/1/2). The part below
    ``position`` stays; ``flip`` keeps the part above instead."""

    axis: int
    position: float
    flip: bool = False

    @property
    def normal(self) -> Vec3:
        """Points to the side that stays."""
        n = [0.0, 0.0, 0.0]
        n[self.axis] = 1.0 if self.flip else -1.0
        return (n[0], n[1], n[2])

    @property
    def origin(self) -> Vec3:
        o = [0.0, 0.0, 0.0]
        o[self.axis] = self.position
        return (o[0], o[1], o[2])

    def keeps(self, lo: Vec3, hi: Vec3) -> bool:
        """Whether anything of the box ``lo``-``hi`` stays visible."""
        a = self.axis
        return hi[a] > self.position if self.flip else lo[a] < self.position


def section_cap(shape: PartShape, plane: SectionPlane, offset: Vec3 = (0, 0, 0)) -> Mesh | None:
    """Cut face of a part (moved by ``offset``) with ``plane``; None if the
    plane misses the part."""
    origin = (
        shape.origin[0] + offset[0],
        shape.origin[1] + offset[1],
        shape.origin[2] + offset[2],
    )
    lo, hi = world_bounds(origin, shape.size, shape.rotation)
    a = plane.axis
    if not lo[a] + 1e-6 < plane.position < hi[a] - 1e-6:
        return None
    if shape.rotation is None:
        local = _local_cap(
            _key(shape.size), shape.features, a, round(plane.position - origin[a], 3)
        )
        if local is None:
            return None
        return Mesh(local.points + np.asarray(origin), local.faces)
    # turned part: cut the placed solid
    solid = placed(make_shape(shape.size, shape.features), origin, shape.rotation)
    corners = np.array(
        [
            [x, y, z]
            for x in (0, shape.size[0])
            for y in (0, shape.size[1])
            for z in (0, shape.size[2])
        ]
    )
    world = to_world(corners, origin, shape.rotation)
    return _cut(solid, a, plane.position, world.min(axis=0) - 1, world.max(axis=0) + 1)


def _key(size: Vec3) -> Vec3:
    return (round(size[0], 3), round(size[1], 3), round(size[2], 3))


@lru_cache(maxsize=512)
def _local_cap(size: Vec3, features: tuple[Feature, ...], axis: int, t: float) -> Mesh | None:
    if not 0 < t < size[axis]:
        return None
    if not features:
        # plain box: a rectangle
        u, v = (axis + 1) % 3, (axis + 2) % 3
        points = np.zeros((4, 3))
        points[:, axis] = t
        points[:, u] = [0, size[u], size[u], 0]
        points[:, v] = [0, 0, size[v], size[v]]
        return Mesh(points, np.array([3, 0, 1, 2, 3, 0, 2, 3], dtype=np.int64))
    lo = np.array([-1.0, -1.0, -1.0])
    hi = np.array(size) + 1
    return _cut(make_shape(size, features), axis, t, lo, hi)


def _cut(solid: object, axis: int, t: float, lo: np.ndarray, hi: np.ndarray) -> Mesh | None:
    """The faces where the plane ``axis = t`` lies inside ``solid``."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    u, v = (axis + 1) % 3, (axis + 2) % 3
    polygon = BRepBuilderAPI_MakePolygon()
    for cu, cv in ((lo[u], lo[v]), (hi[u], lo[v]), (hi[u], hi[v]), (lo[u], hi[v])):
        p = [0.0, 0.0, 0.0]
        p[axis], p[u], p[v] = t, float(cu), float(cv)
        polygon.Add(gp_Pnt(*p))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
    common = BRepAlgoAPI_Common(solid, face)
    if not common.IsDone():  # pragma: no cover - defensive
        return None
    mesh = tessellate(common.Shape())
    return mesh if len(mesh.faces) else None
