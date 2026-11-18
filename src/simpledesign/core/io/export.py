"""3D export: STEP (solids, for other CAD programs), STL and OBJ (meshes).

All formats are in millimetres. Each copy of a pattern becomes its own body;
hidden parts are not exported. STEP keeps the part names (shown in the
browser of e.g. Fusion or FreeCAD), OBJ writes one named object per part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from simpledesign.core.geometry.mesh import box_mesh
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Vec3

STEP_SUFFIXES = (".step", ".stp")
EXPORT_SUFFIXES = (*STEP_SUFFIXES, ".stl", ".obj")


class ExportError(Exception):
    """Writing the export file failed."""


@dataclass(frozen=True, slots=True)
class ExportBody:
    name: str
    origin: Vec3  # min corner
    size: Vec3  # world-axis-aligned size


def export_bodies(doc: Document, evaluation: Evaluation) -> list[ExportBody]:
    bodies: list[ExportBody] = []
    for board in doc.boards.values():
        if not doc.is_visible(board):
            continue
        state = evaluation.boards[board.id]
        origins = state.instance_origins()
        for i, origin in enumerate(origins):
            name = board.name if len(origins) == 1 else f"{board.name}_{i + 1}"
            bodies.append(ExportBody(name, origin, state.size))
    return bodies


def export(doc: Document, evaluation: Evaluation, path: Path) -> None:
    """Write all visible parts; the format follows the file suffix."""
    bodies = export_bodies(doc, evaluation)
    suffix = path.suffix.lower()
    if suffix in STEP_SUFFIXES:
        write_step(bodies, path)
    elif suffix == ".stl":
        write_stl(bodies, path)
    elif suffix == ".obj":
        write_obj(bodies, path)
    else:
        raise ExportError(f"unknown export format: {path.suffix}")


# ====================================================================== STEP
def write_step(bodies: list[ExportBody], path: Path) -> None:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    # An XCAF document carries names (and later colours) into the STEP file.
    cad_doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(cad_doc.Main())
    for body in bodies:
        shape = BRepPrimAPI_MakeBox(gp_Pnt(*body.origin), *body.size).Shape()
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(body.name))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    if not writer.Transfer(cad_doc, STEPControl_AsIs):
        raise ExportError("STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise ExportError(f"cannot write {path}")


# ====================================================================== meshes
def _triangles(body: ExportBody) -> np.ndarray:
    """Triangles of a body as an array of shape (n, 3, 3)."""
    mesh = box_mesh(body.size)
    corners = mesh.faces.reshape(-1, 4)[:, 1:]  # drop the leading "3"
    triangles: np.ndarray = mesh.points[corners] + np.array(body.origin)
    return triangles


_STL_TRIANGLE = np.dtype(
    [("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")]
)


def write_stl(bodies: list[ExportBody], path: Path) -> None:
    """Binary STL: all parts in one mesh (STL has no names or parts)."""
    triangles = np.concatenate([_triangles(b) for b in bodies]) if bodies else np.zeros((0, 3, 3))
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    records = np.zeros(len(triangles), dtype=_STL_TRIANGLE)
    records["normal"] = normals
    records["vertices"] = triangles
    header = b"SimpleDesign STL export (mm)".ljust(80, b" ")
    with path.open("wb") as stream:
        stream.write(header)
        stream.write(np.uint32(len(records)).tobytes())
        stream.write(records.tobytes())


def _obj_name(name: str) -> str:
    return re.sub(r"\s+", "_", name) or "part"


def write_obj(bodies: list[ExportBody], path: Path) -> None:
    lines = ["# SimpleDesign OBJ export, units: mm"]
    offset = 1  # OBJ indices start at 1 and count over the whole file
    for body in bodies:
        mesh = box_mesh(body.size)
        points = mesh.points + np.array(body.origin)
        lines.append(f"o {_obj_name(body.name)}")
        lines += [f"v {x:.4f} {y:.4f} {z:.4f}" for x, y, z in points]
        for a, b, c in mesh.faces.reshape(-1, 4)[:, 1:]:
            lines.append(f"f {a + offset} {b + offset} {c + offset}")
        offset += len(points)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
