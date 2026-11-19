"""Collision check: where do parts overlap?

The machined parts are compared, so wanted overlaps of joints do not count:
a tongue sits in a groove that is cut out of the other part, a tenon in its
mortise. Candidates come from the world bounding boxes; plain unrotated boxes
are intersected directly, everything else with an OpenCascade Boolean.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

from simpledesign.core.geometry.mesh import Mesh, box_mesh, make_shape, tessellate
from simpledesign.core.geometry.placement import placed, world_bounds
from simpledesign.core.model.machining import Feature, PartShape
from simpledesign.core.model.state import Vec3

if TYPE_CHECKING:
    from OCP.TopoDS import TopoDS_Shape

TOLERANCE = 0.01  # mm: boxes must overlap more than this on every axis
MIN_VOLUME = 1.0  # mm³: smaller overlaps are numerical noise (touching faces)


@dataclass(frozen=True, slots=True)
class Collision:
    first: str  # board ids
    second: str
    lo: Vec3  # bounds of the overlap, world coordinates
    hi: Vec3
    volume: float  # mm³
    mesh: Mesh  # the overlapping body, world coordinates


@dataclass(frozen=True, slots=True)
class _Body:
    board_id: str
    shape: PartShape
    lo: Vec3
    hi: Vec3


def find_collisions(
    parts: Mapping[str, list[PartShape]], min_volume: float = MIN_VOLUME
) -> list[Collision]:
    """All overlaps between the parts (also between copies of one pattern)."""
    bodies = []
    for board_id, shapes in parts.items():
        for shape in shapes:
            lo, hi = world_bounds(shape.origin, shape.size, shape.rotation)
            bodies.append(_Body(board_id, shape, lo, hi))
    bodies.sort(key=lambda b: b.lo[0])
    found: list[Collision] = []
    for i, a in enumerate(bodies):
        for b in bodies[i + 1 :]:
            if b.lo[0] >= a.hi[0] - TOLERANCE:
                break  # sorted by x: no later body reaches into a
            if not _boxes_overlap(a, b):
                continue
            hit = _overlap(a.shape, b.shape)
            if hit is None or hit[0] < min_volume:
                continue
            volume, mesh = hit
            low, high = mesh.points.min(axis=0), mesh.points.max(axis=0)
            found.append(
                Collision(
                    a.board_id,
                    b.board_id,
                    (float(low[0]), float(low[1]), float(low[2])),
                    (float(high[0]), float(high[1]), float(high[2])),
                    volume,
                    mesh,
                )
            )
    return found


def _boxes_overlap(a: _Body, b: _Body) -> bool:
    return all(min(a.hi[k], b.hi[k]) - max(a.lo[k], b.lo[k]) > TOLERANCE for k in range(3))


def _plain(shape: PartShape) -> bool:
    return not shape.features and shape.rotation is None


@lru_cache(maxsize=4096)
def _overlap(a: PartShape, b: PartShape) -> tuple[float, Mesh] | None:
    """Volume and body of the overlap of two placed parts (None: none)."""
    if _plain(a) and _plain(b):
        lo = [max(a.origin[k], b.origin[k]) for k in range(3)]
        hi = [min(a.origin[k] + a.size[k], b.origin[k] + b.size[k]) for k in range(3)]
        size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
        if min(size) <= 0:
            return None
        box = box_mesh(size)
        return size[0] * size[1] * size[2], Mesh(box.points + np.asarray(lo), box.faces)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    common = BRepAlgoAPI_Common(_placed(a), _placed(b))
    if not common.IsDone():  # pragma: no cover - defensive
        return None
    result = common.Shape()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(result, props)
    volume = props.Mass()
    if volume <= 0:
        return None
    mesh = tessellate(result)
    if not len(mesh.points):
        return None
    return volume, mesh


def _placed(shape: PartShape) -> TopoDS_Shape:
    return placed(_solid(shape.size, shape.features), shape.origin, shape.rotation)


@lru_cache(maxsize=512)
def _solid(size: Vec3, features: tuple[Feature, ...]) -> TopoDS_Shape:
    return make_shape(size, features)
