"""Triangle meshes of board bodies.

Phase 1 boards are axis-aligned boxes. We build them with OCP directly instead
of build123d: importing build123d takes ~6 s, OCP alone ~0.6 s - and OCP is
preloaded in a background thread at startup anyway (:func:`preload`).

Meshes are cached by size and machining; the viewport places each copy with a
translation, so moving a board or a pattern copy never re-meshes.

Machining (holes, cut-outs from joints) is cut out of the box with boolean
operations. Tools are lengthened a little beyond the part surface so the cut
never leaves a paper-thin skin.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from simpledesign.core.model.machining import Feature, Hole, Prism

if TYPE_CHECKING:
    from OCP.TopoDS import TopoDS_Shape

type Size = tuple[float, float, float]

_OVERCUT = 0.1  # mm: tools reach this far beyond the part surface


@dataclass(frozen=True, slots=True)
class Mesh:
    """Triangles in VTK cell layout: ``faces = [3, a, b, c, 3, …]``."""

    points: npt.NDArray[np.float64]  # shape (n, 3)
    faces: npt.NDArray[np.int64]


def preload() -> threading.Thread:
    """Import the kernel in the background so the first board appears instantly."""

    def load() -> None:
        import OCP.BRepPrimAPI  # noqa: F401

    thread = threading.Thread(target=load, name="ocp-preload", daemon=True)
    thread.start()
    return thread


def _key(size: Size) -> Size:
    # Round to 1/1000 mm so float noise does not create cache misses.
    return (round(size[0], 3), round(size[1], 3), round(size[2], 3))


def box_mesh(size: Size) -> Mesh:
    """Mesh of a box from (0, 0, 0) to ``size``."""
    return _part_mesh(_key(size), ())


def part_mesh(size: Size, features: tuple[Feature, ...] = ()) -> Mesh:
    """Mesh of a machined part: the box (0, 0, 0)-``size`` minus ``features``."""
    return _part_mesh(_key(size), features)


def make_shape(size: Size, features: tuple[Feature, ...] = ()) -> TopoDS_Shape:
    """B-rep solid of a part with its features cut out."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.collections import List_TopoDS_Shape

    shape = BRepPrimAPI_MakeBox(*size).Shape()
    if not features:
        return shape
    tools = List_TopoDS_Shape()
    for feature in features:
        tools.Append(_tool(size, feature))
    arguments = List_TopoDS_Shape()
    arguments.Append(shape)
    cut = BRepAlgoAPI_Cut()
    cut.SetArguments(arguments)
    cut.SetTools(tools)
    cut.Build()
    if not cut.IsDone():  # pragma: no cover - defensive: show the plain part
        return shape
    return cut.Shape()


def _tool(size: Size, feature: Feature) -> TopoDS_Shape:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    if isinstance(feature, Hole):
        direction = [0.0, 0.0, 0.0]
        direction[feature.axis] = float(feature.direction)
        start = list(feature.center)
        start[feature.axis] -= feature.direction * _OVERCUT
        axis = gp_Ax2(gp_Pnt(*start), gp_Dir(*direction))
        return BRepPrimAPI_MakeCylinder(
            axis, feature.diameter / 2, feature.depth + _OVERCUT
        ).Shape()
    if isinstance(feature, Prism):
        return _prism_tool(size, feature)
    lo, hi = list(feature.lo), list(feature.hi)
    for i in range(3):
        # Open where the cut-out reaches the part surface.
        if lo[i] <= 1e-6:
            lo[i] -= _OVERCUT
        if hi[i] >= size[i] - 1e-6:
            hi[i] += _OVERCUT
    return BRepPrimAPI_MakeBox(gp_Pnt(*lo), gp_Pnt(*hi)).Shape()


def _prism_tool(size: Size, prism: Prism) -> TopoDS_Shape:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    a = prism.axis
    start = prism.points[0][a]
    length = prism.length
    if start <= 1e-6:
        start -= _OVERCUT
        length += _OVERCUT
    if prism.points[0][a] + prism.length >= size[a] - 1e-6:
        length += _OVERCUT
    polygon = BRepBuilderAPI_MakePolygon()
    points = prism.points
    for n, point in enumerate(points):
        q = list(point)
        q[a] = start
        for i in range(3):
            # Open where a corner lies on the part surface: move it outwards along
            # the edge that leaves the surface, so slanted sides keep their angle.
            outside = point[i] <= 1e-6 or point[i] >= size[i] - 1e-6
            if i == a or not outside:
                continue
            for other in (points[n - 1], points[(n + 1) % len(points)]):
                step = point[i] - other[i]
                if abs(step) > 1e-6:
                    scale = _OVERCUT / abs(step)
                    for j in range(3):
                        if j != a:
                            q[j] += (point[j] - other[j]) * scale
                    break
        polygon.Add(gp_Pnt(*q))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    sweep = [0.0, 0.0, 0.0]
    sweep[a] = length
    return BRepPrimAPI_MakePrism(face, gp_Vec(*sweep)).Shape()


@lru_cache(maxsize=512)
def _part_mesh(size: Size, features: tuple[Feature, ...]) -> Mesh:
    return tessellate(make_shape(size, features))


def tessellate(shape: TopoDS_Shape) -> Mesh:
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    BRepMesh_IncrementalMesh(shape, 0.1, False, 0.2, True)
    points: list[tuple[float, float, float]] = []
    faces: list[int] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            offset = len(points)
            transform = location.Transformation()
            for k in range(1, triangulation.NbNodes() + 1):
                p = triangulation.Node(k).Transformed(transform)
                points.append((p.X(), p.Y(), p.Z()))
            reverse = face.Orientation() == TopAbs_REVERSED
            for k in range(1, triangulation.NbTriangles() + 1):
                a, b, c = triangulation.Triangle(k).Get()
                if reverse:
                    b, c = c, b
                faces += [3, a - 1 + offset, b - 1 + offset, c - 1 + offset]
        explorer.Next()
    return Mesh(np.array(points, dtype=np.float64), np.array(faces, dtype=np.int64))
