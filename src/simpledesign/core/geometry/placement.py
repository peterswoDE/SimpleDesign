"""World placement of a part: its local box (0..size) rotated about the
origin corner (free rotation), then moved to ``origin``."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from simpledesign.core.model.state import Vec3

if TYPE_CHECKING:
    from OCP.gp import gp_Trsf
    from OCP.TopoDS import TopoDS_Shape

type Rotation = tuple[int, float] | None  # (world axis 0/1/2, degrees)


def rotation_matrix(rotation: Rotation) -> np.ndarray:
    matrix = np.eye(3)
    if rotation is None:
        return matrix
    axis, degrees = rotation
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    matrix[i, i], matrix[i, j] = c, -s
    matrix[j, i], matrix[j, j] = s, c
    return matrix


def to_world(points: np.ndarray, origin: Vec3, rotation: Rotation) -> np.ndarray:
    """Local points (n, 3) -> world."""
    if rotation is not None:
        points = points @ rotation_matrix(rotation).T
    result: np.ndarray = points + np.asarray(origin)
    return result


def world_bounds(origin: Vec3, size: Vec3, rotation: Rotation) -> tuple[Vec3, Vec3]:
    corners = np.array(
        [[x, y, z] for x in (0, size[0]) for y in (0, size[1]) for z in (0, size[2])]
    )
    world = to_world(corners, origin, rotation)
    lo, hi = world.min(axis=0), world.max(axis=0)
    return (float(lo[0]), float(lo[1]), float(lo[2])), (float(hi[0]), float(hi[1]), float(hi[2]))


def gp_placement(origin: Vec3, rotation: Rotation) -> gp_Trsf:
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*origin))
    if rotation is None:
        return move
    axis, degrees = rotation
    direction = [0.0, 0.0, 0.0]
    direction[axis] = 1.0
    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*direction)), math.radians(degrees))
    return move.Multiplied(turn)


def placed(shape: TopoDS_Shape, origin: Vec3, rotation: Rotation) -> TopoDS_Shape:
    from OCP.TopLoc import TopLoc_Location

    return shape.Moved(TopLoc_Location(gp_placement(origin, rotation)))
