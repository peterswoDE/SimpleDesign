"""Built-in materials (phase 1). A user-editable library follows in phase 2.

Built-in IDs are stable strings, not UUIDs, so they stay recognisable in files.
"""

from __future__ import annotations

from simpledesign.core.materials.material import Material, MaterialKind

BUILTIN_MATERIALS: tuple[Material, ...] = (
    Material(
        "builtin.multiplex_birke_18",
        "Multiplex Birke 18 mm",
        MaterialKind.PANEL,
        18,
        None,
        True,
        "#e3c99a",
    ),
    Material(
        "builtin.multiplex_birke_12",
        "Multiplex Birke 12 mm",
        MaterialKind.PANEL,
        12,
        None,
        True,
        "#e3c99a",
    ),
    Material(
        "builtin.leimholz_buche_18",
        "Leimholz Buche 18 mm",
        MaterialKind.PANEL,
        18,
        None,
        True,
        "#d9a877",
    ),
    Material(
        "builtin.leimholz_buche_27",
        "Leimholz Buche 27 mm",
        MaterialKind.PANEL,
        27,
        None,
        True,
        "#d9a877",
    ),
    Material(
        "builtin.leimholz_fichte_18",
        "Leimholz Fichte 18 mm",
        MaterialKind.PANEL,
        18,
        None,
        True,
        "#ecd5a6",
    ),
    Material("builtin.mdf_19", "MDF 19 mm", MaterialKind.PANEL, 19, None, False, "#b89a7a"),
    Material(
        "builtin.spanplatte_19",
        "Spanplatte 19 mm",
        MaterialKind.PANEL,
        19,
        None,
        False,
        "#cbb38f",
    ),
    Material("builtin.hdf_3", "HDF 3 mm", MaterialKind.PANEL, 3, None, False, "#8f7258"),
    Material(
        "builtin.eiche_massiv_26",
        "Eiche massiv 26 mm",
        MaterialKind.SOLID,
        26,
        None,
        True,
        "#c19a6b",
    ),
    Material("builtin.kvh_60x80", "KVH 60 x 80", MaterialKind.TIMBER, 60, 80, True, "#e8cf9b"),
    Material("builtin.kvh_60x120", "KVH 60 x 120", MaterialKind.TIMBER, 60, 120, True, "#e8cf9b"),
    Material(
        "builtin.dachlatte_24x48",
        "Dachlatte 24 x 48",
        MaterialKind.TIMBER,
        24,
        48,
        True,
        "#e8cf9b",
    ),
)

DEFAULT_MATERIAL_ID = "builtin.multiplex_birke_18"


def builtin_material(material_id: str) -> Material:
    for material in BUILTIN_MATERIALS:
        if material.id == material_id:
            return material
    raise KeyError(material_id)
