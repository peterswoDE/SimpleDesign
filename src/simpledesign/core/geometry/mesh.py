"""Triangle meshes of board bodies.

Phase 1 boards are axis-aligned boxes. We build them with OCP directly instead
of build123d: importing build123d takes ~6 s, OCP alone ~0.6 s - and OCP is
preloaded in a background thread at startup anyway (:func:`preload`).

Meshes are cached by size; the viewport places each copy with a translation,
so moving a board or a pattern copy never re-meshes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import numpy.typing as npt

type Size = tuple[float, float, float]


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
    return _box_mesh(_key(size))


@lru_cache(maxsize=512)
def _box_mesh(size: Size) -> Mesh:
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    shape = BRepPrimAPI_MakeBox(*size).Shape()
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
