"""Material library: built-in materials plus the user's own, stored as JSON.

On first start the library contains the built-in materials; the user may edit
or delete them and add own stock (``Materialbibliothek`` dialog). Built-in IDs
are stable strings, not UUIDs, so they stay recognisable in files.

Documents keep *copies* of the materials they use (see :mod:`document`), so a
file still opens after a material was changed or deleted here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from simpledesign.core.materials.material import (
    Material,
    MaterialKind,
    material_from_dict,
    material_to_dict,
)

LIBRARY_FORMAT_VERSION = 1

PANEL = MaterialKind.PANEL
SOLID = MaterialKind.SOLID
TIMBER = MaterialKind.TIMBER

BUILTIN_MATERIALS: tuple[Material, ...] = (
    Material(
        "builtin.multiplex_birke_18", "Multiplex Birke 18 mm", PANEL, 18, None, True, "#e3c99a",
        stock_length=2500, stock_width=1250, edge_trim=10,
    ),
    Material(
        "builtin.multiplex_birke_12", "Multiplex Birke 12 mm", PANEL, 12, None, True, "#e3c99a",
        stock_length=2500, stock_width=1250, edge_trim=10,
    ),
    Material(
        "builtin.leimholz_buche_18", "Leimholz Buche 18 mm", PANEL, 18, None, True, "#d9a877",
        stock_length=2000, stock_width=600, edge_trim=5,
    ),
    Material(
        "builtin.leimholz_buche_27", "Leimholz Buche 27 mm", PANEL, 27, None, True, "#d9a877",
        stock_length=2000, stock_width=600, edge_trim=5,
    ),
    Material(
        "builtin.leimholz_fichte_18", "Leimholz Fichte 18 mm", PANEL, 18, None, True, "#ecd5a6",
        stock_length=2000, stock_width=600, edge_trim=5,
    ),
    Material(
        "builtin.mdf_19", "MDF 19 mm", PANEL, 19, None, False, "#b89a7a",
        stock_length=2800, stock_width=2070, edge_trim=10,
    ),
    Material(
        "builtin.spanplatte_19", "Spanplatte 19 mm", PANEL, 19, None, False, "#cbb38f",
        stock_length=2800, stock_width=2070, edge_trim=10,
    ),
    Material(
        "builtin.hdf_3", "HDF 3 mm", PANEL, 3, None, False, "#8f7258",
        stock_length=2800, stock_width=2070, edge_trim=10,
    ),
    Material(
        "builtin.eiche_massiv_26", "Eiche massiv 26 mm", SOLID, 26, None, True, "#c19a6b",
        stock_length=2500, stock_width=250, edge_trim=15,
    ),
    Material(
        "builtin.kvh_60x80", "KVH 60 x 80", TIMBER, 60, 80, True, "#e8cf9b",
        stock_length=5000,
    ),
    Material(
        "builtin.kvh_60x120", "KVH 60 x 120", TIMBER, 60, 120, True, "#e8cf9b",
        stock_length=5000,
    ),
    Material(
        "builtin.dachlatte_24x48", "Dachlatte 24 x 48", TIMBER, 24, 48, True, "#e8cf9b",
        stock_length=4000,
    ),
)  # fmt: skip

DEFAULT_MATERIAL_ID = "builtin.multiplex_birke_18"


def builtin_material(material_id: str) -> Material:
    for material in BUILTIN_MATERIALS:
        if material.id == material_id:
            return material
    raise KeyError(material_id)


class LibraryError(Exception):
    """The library file is unreadable or invalid."""


class MaterialLibrary:
    """Ordered collection of materials (order = display order)."""

    def __init__(self, materials: Iterable[Material] = BUILTIN_MATERIALS) -> None:
        self.materials: dict[str, Material] = {m.id: m for m in materials}

    def __iter__(self) -> Iterator[Material]:
        return iter(self.materials.values())

    def __len__(self) -> int:
        return len(self.materials)

    def __contains__(self, material_id: object) -> bool:
        return material_id in self.materials

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MaterialLibrary) and list(self) == list(other)

    def get(self, material_id: str) -> Material | None:
        return self.materials.get(material_id)

    def default(self) -> Material:
        """The material for new boards: the built-in default, else the first one."""
        if DEFAULT_MATERIAL_ID in self.materials:
            return self.materials[DEFAULT_MATERIAL_ID]
        if self.materials:
            return next(iter(self.materials.values()))
        return builtin_material(DEFAULT_MATERIAL_ID)

    def copy(self) -> MaterialLibrary:
        return MaterialLibrary(self.materials.values())

    # ------------------------------------------------------------------ files
    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": LIBRARY_FORMAT_VERSION,
            "materials": [material_to_dict(m) for m in self.materials.values()],
        }

    @classmethod
    def from_dict(cls, data: object) -> MaterialLibrary:
        if not isinstance(data, dict):
            raise LibraryError("invalid material library")
        try:
            if data["format_version"] > LIBRARY_FORMAT_VERSION:
                raise LibraryError("library file is newer than this program")
            return cls(material_from_dict(m) for m in data["materials"])
        except (KeyError, TypeError, ValueError) as error:
            raise LibraryError(f"invalid material library: {error!r}") from error

    @classmethod
    def load(cls, path: Path) -> MaterialLibrary:
        """A missing file gives the built-in library."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LibraryError(f"cannot read material library: {error}") from error
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(self.to_dict(), stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
