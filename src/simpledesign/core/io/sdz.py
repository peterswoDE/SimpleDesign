"""The ``.sdz`` file format: UTF-8 JSON.

Stored are formulas (with references as IDs) - never computed values, so a
file always reproduces the same model. ``format_version`` is raised on every
incompatible change; :data:`MIGRATIONS` upgrades old files step by step, so
files from older versions keep opening.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from simpledesign.core.materials.edgeband import (
    BandEdge,
    edge_band_from_dict,
    edge_band_to_dict,
)
from simpledesign.core.materials.library import BUILTIN_MATERIALS
from simpledesign.core.materials.material import material_from_dict, material_to_dict
from simpledesign.core.model import fitting as fittings
from simpledesign.core.model.document import Document
from simpledesign.core.model.fitting import Fitting, FittingKind
from simpledesign.core.model.joint import Joint, JointKind, default_params
from simpledesign.core.model.part import (
    EDITABLE_PROPERTIES,
    Axis,
    Board,
    Face,
    Group,
    Orientation,
    Parameter,
    SketchCut,
    default_expressions,
    is_sketch_property,
)
from simpledesign.core.model.settings import CutSettings
from simpledesign.core.sketch.model import from_dict as sketch_from_dict
from simpledesign.core.sketch.model import to_dict as sketch_to_dict

FORMAT_VERSION = 5
FILE_SUFFIX = ".sdz"

type Json = dict[str, Any]


def _migrate_1_to_2(data: Json) -> Json:
    """Phase 2: stock size/price per material, grain override, cut settings."""
    builtins = {m.id: material_to_dict(m) for m in BUILTIN_MATERIALS}
    for m in data["materials"]:
        # Version-1 files only knew built-in materials: take their stock data.
        known = builtins.get(m["id"], {})
        for key in ("stock_length", "stock_width", "edge_trim", "price"):
            m.setdefault(key, known.get(key, 0.0 if key == "edge_trim" else None))
    for b in data["boards"]:
        b.setdefault("ignore_grain", False)
    data.setdefault("cut_settings", {"kerf": 4.0, "crosscut_allowance": 10.0})
    return data


def _migrate_2_to_3(data: Json) -> Json:
    """Phase 3: joints."""
    data.setdefault("joints", [])
    return data


# MIGRATIONS[n] upgrades a version-n dict to version n + 1.
def _migrate_3_to_4(data: Json) -> Json:
    # Version 4 adds sketches (profile, cuts) to boards; old boards have none.
    return data


def _migrate_4_to_5(data: Json) -> Json:
    """Phase 9: fittings (hinges, slides, shelf pin rows)."""
    data.setdefault("fittings", [])
    return data


MIGRATIONS: dict[int, Callable[[Json], Json]] = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
    3: _migrate_3_to_4,
    4: _migrate_4_to_5,
}


class FileFormatError(Exception):
    """The file is not a valid (or a too new) SimpleDesign file."""


def to_dict(doc: Document) -> Json:
    return {
        "format_version": FORMAT_VERSION,
        "parameters": [
            {"id": p.id, "name": p.name, "expression": p.expression, "group_id": p.group_id}
            for p in doc.parameters.values()
        ],
        "groups": [
            {"id": g.id, "name": g.name, "parent_id": g.parent_id, "visible": g.visible}
            for g in doc.groups.values()
        ],
        "boards": [
            {
                "id": b.id,
                "name": b.name,
                "material_id": b.material_id,
                "group_id": b.group_id,
                "orientation": b.orientation.value,
                "expressions": dict(b.expressions),
                "pattern_axis": b.pattern_axis.value,
                "rotation_axis": b.rotation_axis.value,
                "visible": b.visible,
                "transparent": b.transparent,
                "ignore_grain": b.ignore_grain,
                "back_panel": b.back_panel,
                "through_back": b.through_back,
                "profile": None if b.profile is None else sketch_to_dict(b.profile),
                "cuts": [
                    {
                        "id": c.id,
                        "face": c.face.value,
                        "through": c.through,
                        "sketch": sketch_to_dict(c.sketch),
                    }
                    for c in b.cuts
                ],
                "edges": {edge.value: band for edge, band in b.edges.items()},
            }
            for b in doc.boards.values()
        ],
        "materials": [material_to_dict(m) for m in doc.materials.values()],
        # own bands and every band in use: the file stays readable on its own
        "edge_bands": [edge_band_to_dict(doc.edge_band(i)) for i in _band_ids(doc)],
        "joints": [
            {
                "id": j.id,
                "kind": j.kind.value,
                "main_id": j.main_id,
                "attached_id": j.attached_id,
                "params": dict(j.params),
            }
            for j in doc.joints.values()
        ],
        "fittings": [
            {
                "id": f.id,
                "kind": f.kind.value,
                "part_id": f.part_id,
                "carrier_id": f.carrier_id,
                "side": f.side,
                "params": dict(f.params),
            }
            for f in doc.fittings.values()
        ],
        "cut_settings": {
            "kerf": doc.cut_settings.kerf,
            "crosscut_allowance": doc.cut_settings.crosscut_allowance,
        },
    }


def _band_ids(doc: Document) -> list[str]:
    used = {band for b in doc.boards.values() for band in b.edges.values()}
    return sorted(used | doc.edge_bands.keys())


def from_dict(data: Json) -> Document:
    data = migrate(data)
    doc = Document()
    try:
        for m in data["materials"]:
            material = material_from_dict(m)
            doc.materials[material.id] = material
        for e in data.get("edge_bands", []):
            band = edge_band_from_dict(e)
            doc.edge_bands[band.id] = band
        settings = data["cut_settings"]
        doc.cut_settings = CutSettings(
            kerf=float(settings["kerf"]),
            crosscut_allowance=float(settings["crosscut_allowance"]),
        )
        for g in data["groups"]:
            doc.groups[g["id"]] = Group(g["id"], g["name"], g["parent_id"], bool(g["visible"]))
        for p in data["parameters"]:
            doc.parameters[p["id"]] = Parameter(p["id"], p["name"], p["expression"], p["group_id"])
        for b in data["boards"]:
            expressions = default_expressions()
            expressions.update(
                {
                    k: str(v)
                    for k, v in b["expressions"].items()
                    if k in EDITABLE_PROPERTIES or is_sketch_property(k)
                }
            )
            profile = b.get("profile")
            doc.boards[b["id"]] = Board(
                id=b["id"],
                name=b["name"],
                material_id=b["material_id"],
                group_id=b["group_id"],
                orientation=Orientation(b["orientation"]),
                expressions=expressions,
                pattern_axis=Axis(b["pattern_axis"]),
                rotation_axis=Axis(b.get("rotation_axis", "z")),
                visible=bool(b["visible"]),
                transparent=bool(b["transparent"]),
                ignore_grain=bool(b["ignore_grain"]),
                back_panel=bool(b.get("back_panel", False)),
                through_back=bool(b.get("through_back", False)),
                profile=None if profile is None else sketch_from_dict(profile),
                cuts=[
                    SketchCut(
                        c["id"], sketch_from_dict(c["sketch"]), Face(c["face"]), bool(c["through"])
                    )
                    for c in b.get("cuts", [])
                ],
                edges={BandEdge(k): str(v) for k, v in b.get("edges", {}).items()},
            )
        for j in data["joints"]:
            kind = JointKind(j["kind"])
            params = default_params(kind)
            params.update({k: float(v) for k, v in j["params"].items() if k in params})
            doc.joints[j["id"]] = Joint(j["id"], kind, j["main_id"], j["attached_id"], params)
        for f in data["fittings"]:
            fitting_kind = FittingKind(f["kind"])
            values = fittings.default_params(fitting_kind)
            values.update({k: float(v) for k, v in f["params"].items() if k in values})
            carrier = f["carrier_id"]
            doc.fittings[f["id"]] = Fitting(
                f["id"],
                fitting_kind,
                str(f["part_id"]),
                None if carrier is None else str(carrier),
                1 if int(f["side"]) >= 0 else -1,
                values,
            )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise FileFormatError(f"invalid file content: {error!r}") from error
    _check_references(doc)
    return doc


def _check_references(doc: Document) -> None:
    for g in doc.groups.values():
        if g.parent_id is not None and g.parent_id not in doc.groups:
            raise FileFormatError(f"group {g.id} has an unknown parent")
    owners: list[Parameter | Board] = [*doc.parameters.values(), *doc.boards.values()]
    for owner in owners:
        if owner.group_id is not None and owner.group_id not in doc.groups:
            raise FileFormatError(f"{owner.id} is in an unknown group")
    for b in doc.boards.values():
        if b.material_id not in doc.materials:
            raise FileFormatError(f"board {b.id} has an unknown material")
        for band_id in b.edges.values():
            try:
                doc.edge_band(band_id)
            except KeyError:
                raise FileFormatError(f"board {b.id} has an unknown edge band") from None
    for j in doc.joints.values():
        if j.main_id not in doc.boards or j.attached_id not in doc.boards:
            raise FileFormatError(f"joint {j.id} refers to an unknown board")
    for f in doc.fittings.values():
        if any(b not in doc.boards for b in f.board_ids()):
            raise FileFormatError(f"fitting {f.id} refers to an unknown board")


def migrate(data: Json) -> Json:
    version = data.get("format_version")
    if not isinstance(version, int) or version < 1:
        raise FileFormatError("missing format_version")
    if version > FORMAT_VERSION:
        raise FileFormatError(f"file version {version} is newer than this program")
    while version < FORMAT_VERSION:
        try:
            data = MIGRATIONS[version](data)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise FileFormatError(f"invalid version-{version} file: {error!r}") from error
        version += 1
        data["format_version"] = version
    return data


def save(doc: Document, path: Path, extra: Json | None = None) -> None:
    """Write atomically: a crash while saving never destroys the old file.

    ``extra`` adds top-level keys (autosave stores the original file path).
    """
    data = to_dict(doc)
    if extra:
        data.update(extra)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def load_json(path: Path) -> Json:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FileFormatError(f"not a SimpleDesign file: {error}") from error
    if not isinstance(data, dict):
        raise FileFormatError("not a SimpleDesign file")
    return data


def load(path: Path) -> Document:
    return from_dict(load_json(path))
