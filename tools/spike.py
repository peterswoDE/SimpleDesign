"""Phase 0 technology spike.

Checks that the chosen libraries work together and measures whether
"live" recomputation (< 100 ms for ~50 parts) is realistic.

Run:  uv run python -W always::DeprecationWarning tools/spike.py
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import numpy as np

warnings.simplefilter("always", DeprecationWarning)
warnings.simplefilter("always", PendingDeprecationWarning)

RESULTS: list[tuple[str, str]] = []


@contextmanager
def timed(label: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    ms = (time.perf_counter() - start) * 1000
    RESULTS.append((label, f"{ms:8.1f} ms"))


def note(label: str, value: object) -> None:
    RESULTS.append((label, str(value)))


# --------------------------------------------------------------------------- imports
with timed("Import build123d"):
    from build123d import Align, Box, Cylinder, Pos, Solid
with timed("Import pyvista + vtk"):
    import pyvista as pv
    import vtk
with timed("Import slvs"):
    import slvs


# --------------------------------------------------------------------------- geometry
MIN3 = (Align.MIN, Align.MIN, Align.MIN)


def make_board(i: int, holes: bool) -> Solid:
    board = Pos(i * 30.0, 0, 0) * Box(18, 400, 1800, align=MIN3)
    if holes:
        for z in (50.0, 250.0, 1550.0, 1750.0):
            board -= Pos(i * 30.0 + 9, 200, z) * Cylinder(4, 40, rotation=(90, 0, 0))
    return board  # type: ignore[return-value]


def to_polydata_b123d(shape: Solid) -> pv.PolyData:
    """Variant A: build123d's own tessellate()."""
    verts, tris = shape.tessellate(tolerance=0.1, angular_tolerance=0.2)
    points = np.array([(v.X, v.Y, v.Z) for v in verts], dtype=float)
    faces = np.hstack([[3, *t] for t in tris]).astype(np.int64)
    return pv.PolyData(points, faces)


def to_polydata(shape: Solid) -> pv.PolyData:
    """Variant B: mesh directly via OCP (no per-vertex Vector objects)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    occ = shape.wrapped
    BRepMesh_IncrementalMesh(occ, 0.1, False, 0.2, True)
    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    offset = 0
    explorer = TopExp_Explorer(occ, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            n = tri.NbNodes()
            for k in range(1, n + 1):
                p = tri.Node(k).Transformed(trsf)
                points.append((p.X(), p.Y(), p.Z()))
            reverse = face.Orientation() == TopAbs_REVERSED
            for k in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(k).Get()
                if reverse:
                    b, c = c, b
                faces.append((3, a - 1 + offset, b - 1 + offset, c - 1 + offset))
            offset += n
        explorer.Next()
    return pv.PolyData(np.array(points), np.array(faces, dtype=np.int64))


def bench(label: str, fn: Callable[[], object], repeat: int = 3) -> object:
    best = float("inf")
    result: object = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    RESULTS.append((label, f"{best * 1000:8.1f} ms"))
    return result


# Every measurement creates fresh shapes: OCC caches triangulations on a shape,
# so re-meshing the same shape would look unrealistically fast.
boards = bench(
    "50 Bretter erzeugen (ohne Bohrungen)", lambda: [make_board(i, False) for i in range(50)]
)
assert isinstance(boards, list)
bench(
    "50 Bretter erzeugen + tessellieren (build123d)",
    lambda: [to_polydata_b123d(make_board(i, False)) for i in range(50)],
)
bench(
    "50 Bretter erzeugen + tessellieren (OCP direkt)",
    lambda: [to_polydata(make_board(i, False)) for i in range(50)],
)
holed = bench(
    "50 Bretter mit je 4 Bohrungen (Boolean)", lambda: [make_board(i, True) for i in range(50)]
)
assert isinstance(holed, list)
bench(
    "50 gebohrte Bretter erzeugen + tessellieren (OCP)",
    lambda: [to_polydata(make_board(i, True)) for i in range(50)],
)
bench("1 Brett ändern ohne Bohrungen (OCP)", lambda: to_polydata(make_board(3, False)), 10)
bench("1 Brett ändern mit 4 Bohrungen (OCP)", lambda: to_polydata(make_board(3, True)), 10)
bench(
    "10 Bretter ändern ohne Bohrungen (typische Höhenänderung)",
    lambda: [to_polydata(make_board(i, False)) for i in range(10)],
)
note("Volumen Brett (soll 18*400*1800=12960000)", round(boards[0].volume))

# --------------------------------------------------------------------------- render + picking
pv.OFF_SCREEN = True
meshes = [to_polydata(b) for b in boards]
plotter = pv.Plotter(off_screen=True, window_size=(800, 600))
actors = [plotter.add_mesh(m, color="tan") for m in meshes]
plotter.view_xz()
with timed("Erstes Rendern (offscreen, 50 Teile)"):
    plotter.show(auto_close=False)
with timed("Erneutes Rendern"):
    plotter.render()

target = 10
# Project a point on the *visible front face* (y = 0). Projecting the volume
# center would hit the neighbouring board because of perspective.
cx, _, cz = meshes[target].center
renderer = plotter.renderer
renderer.SetWorldPoint(cx, 0.0, cz, 1.0)
renderer.WorldToDisplay()
dx, dy, _ = renderer.GetDisplayPoint()
picker = vtk.vtkCellPicker()
picker.SetTolerance(0.0005)
picker.Pick(dx, dy, 0, renderer)
picked_ok = picker.GetActor() is actors[target]
note("Picking trifft richtiges Teil", picked_ok)
plotter.close()

# --------------------------------------------------------------------------- sketch solver
# Rectangle: first point fixed (group 1), rest solved (group 2).
# Constraints: horizontal/vertical edges, width 600, height 400.
slvs.clear_sketch()
g_fixed, g_solve = 1, 2
wp = slvs.add_base_2d(g_fixed)
p0 = slvs.add_point_2d(g_fixed, 0, 0, wp)
p1 = slvs.add_point_2d(g_solve, 500, 10, wp)
p2 = slvs.add_point_2d(g_solve, 480, 300, wp)
p3 = slvs.add_point_2d(g_solve, 5, 320, wp)
lines = [slvs.add_line_2d(g_solve, a, b, wp) for a, b in ((p0, p1), (p1, p2), (p2, p3), (p3, p0))]
slvs.horizontal(g_solve, lines[0], wp)
slvs.vertical(g_solve, lines[1], wp)
slvs.horizontal(g_solve, lines[2], wp)
slvs.vertical(g_solve, lines[3], wp)
slvs.distance(g_solve, p0, p1, 600, wp)
slvs.distance(g_solve, p1, p2, 400, wp)
with timed("Skizze lösen (Rechteck, 6 Constraints)"):
    res = slvs.solve_sketch(g_solve, False)
note(
    "Solver-Ergebnis / Freiheitsgrade", f"{slvs.ResultFlag(res['result']).name} / dof={res['dof']}"
)
# A 2D point only has 2 valid params; the remaining entries are 0 placeholders
# and reading them aborts the whole process (assertion inside SolveSpace).
p2_xy = tuple(round(slvs.get_param_value(h), 3) for h in p2["param"][:2])
note("Punkt 2 (soll 600, 400)", p2_xy)

# --------------------------------------------------------------------------- report
width = max(len(label) for label, _ in RESULTS)
print()
for label, value in RESULTS:
    print(f"{label:<{width}}  {value}")
