"""DXF export of drawing sheets (ezdxf, MIT licensed).

Units are millimetres; the sheets are placed side by side in model space,
each on paper scale (as printed). Layers separate visible and hidden edges,
dimensions, text and the frame so they can be switched in a CAD program.
"""

from __future__ import annotations

from pathlib import Path

from ezdxf import units
from ezdxf.enums import TextEntityAlignment
from ezdxf.filemanagement import new
from ezdxf.layouts.layout import Modelspace

from simpledesign.core.drawing.sheet import Align, Polyline, Sheet, Style, Text, expand

LAYERS: dict[Style | str, tuple[str, int, str]] = {
    # style -> (layer, ACI colour, linetype)
    Style.VISIBLE: ("SICHTBAR", 7, "CONTINUOUS"),
    Style.HIDDEN: ("VERDECKT", 8, "DASHED"),
    Style.THIN: ("MASS", 3, "CONTINUOUS"),
    Style.FRAME: ("RAHMEN", 7, "CONTINUOUS"),
    "text": ("TEXT", 7, "CONTINUOUS"),
}
LINEWEIGHT = {Style.VISIBLE: 50, Style.HIDDEN: 25, Style.THIN: 18, Style.FRAME: 50}
SHEET_GAP = 20.0

_ALIGN = {
    Align.LEFT: TextEntityAlignment.BOTTOM_LEFT,
    Align.CENTER: TextEntityAlignment.BOTTOM_CENTER,
    Align.RIGHT: TextEntityAlignment.BOTTOM_RIGHT,
}


def write_dxf(sheets: list[Sheet], path: Path) -> None:
    doc = new("R2018", setup=True)  # setup: standard linetypes and text styles
    doc.units = units.MM
    for layer, color, linetype in LAYERS.values():
        doc.layers.add(layer, color=color, linetype=linetype)
    msp = doc.modelspace()
    x0 = 0.0
    for sheet in sheets:
        for item in sheet.items:
            for prim in expand(item):
                if isinstance(prim, Polyline):
                    layer = LAYERS[prim.style][0]
                    points = [(x0 + x, y) for x, y in prim.points]
                    msp.add_lwpolyline(
                        points,
                        close=prim.closed,
                        dxfattribs={"layer": layer, "lineweight": LINEWEIGHT[prim.style]},
                    )
                else:
                    _text(msp, prim, x0)
        x0 += sheet.size[0] + SHEET_GAP
    doc.saveas(path)


def _text(msp: Modelspace, text: Text, x0: float) -> None:
    entity = msp.add_text(
        text.text,
        height=text.height,
        rotation=text.rotation,
        dxfattribs={"layer": LAYERS["text"][0], "style": "OpenSans"},
    )
    entity.set_placement((x0 + text.pos[0], text.pos[1]), align=_ALIGN[text.align])
