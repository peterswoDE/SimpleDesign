"""Furniture library: template previews to drag into the 3D view."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.templates.library import TemplateLibrary
from simpledesign.core.templates.template import Template, TemplateError

MIME_TYPE = "application/x-simpledesign-template"
ICON_SIZE = 96
KEY_ROLE = Qt.ItemDataRole.UserRole

Vec3 = tuple[float, float, float]

# Iso view as in the 3D view: looking from (1, -1, 1) with Z up.
_VIEW = (1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3))
_RIGHT = (1 / math.sqrt(2), 1 / math.sqrt(2), 0.0)
_UP = (-1 / math.sqrt(6), 1 / math.sqrt(6), 2 / math.sqrt(6))
# Visible faces in that view: (+X, -Y, +Z) with a shading factor each.
_FACES: tuple[tuple[int, float, float], ...] = ((0, 1.0, 0.82), (1, 0.0, 0.68), (2, 1.0, 1.0))


def template_key(template: Template) -> str:
    """Identifies a template in the library (built-in and own may share a name)."""
    return ("builtin:" if template.builtin else "own:") + template.name


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def preview_faces(template: Template) -> list[tuple[float, list[Vec3], str, float]]:
    """The visible box faces of all parts: (depth, corners, color, shade),
    sorted far to near (painter's algorithm)."""
    doc = template.document()
    evaluation = Evaluator().evaluate(doc)
    faces: list[tuple[float, list[Vec3], str, float]] = []
    for board_id, state in evaluation.boards.items():
        board = doc.boards[board_id]
        if not board.visible:
            continue
        material = doc.materials.get(board.material_id)
        color = material.color if material is not None else "#e3c99a"
        size = state.size
        for origin in state.instance_origins():
            low = origin
            high = (origin[0] + size[0], origin[1] + size[1], origin[2] + size[2])
            for axis, side, shade in _FACES:
                level = (low, high)[int(side)][axis]
                u, v = [a for a in range(3) if a != axis]
                corners: list[Vec3] = []
                for cu, cv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    point = [0.0, 0.0, 0.0]
                    point[axis] = level
                    point[u] = (low, high)[cu][u]
                    point[v] = (low, high)[cv][v]
                    corners.append((point[0], point[1], point[2]))
                center = tuple(sum(c[i] for c in corners) / 4 for i in range(3))
                faces.append(
                    (_dot((center[0], center[1], center[2]), _VIEW), corners, color, shade)
                )
    faces.sort(key=lambda face: face[0])
    return faces


def render_preview(template: Template, size: int = ICON_SIZE) -> QPixmap:
    """A small iso drawing of the template's parts."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    try:
        faces = preview_faces(template)
    except TemplateError, KeyError, ValueError:
        return pixmap
    if not faces:
        return pixmap
    projected = [
        [(_dot(c, _RIGHT), -_dot(c, _UP)) for c in corners] for _d, corners, _c, _s in faces
    ]
    xs = [p[0] for poly in projected for p in poly]
    ys = [p[1] for poly in projected for p in poly]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    margin = 4
    scale = (size - 2 * margin) / span
    dx = (size - (max(xs) - min(xs)) * scale) / 2 - min(xs) * scale
    dy = (size - (max(ys) - min(ys)) * scale) / 2 - min(ys) * scale
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(60, 45, 30, 140))
    pen.setWidthF(0.6)
    painter.setPen(pen)
    for (_depth, _corners, color, shade), poly in zip(faces, projected, strict=True):
        base = QColor(color)
        painter.setBrush(
            QColor.fromRgbF(base.redF() * shade, base.greenF() * shade, base.blueF() * shade)
        )
        painter.drawPolygon(QPolygonF([QPointF(x * scale + dx, y * scale + dy) for x, y in poly]))
    painter.end()
    return pixmap


class LibraryList(QListWidget):
    """Icon list whose items can be dragged into the 3D view."""

    def mimeTypes(self) -> list[str]:
        return [MIME_TYPE]

    def mimeData(self, items: Sequence[QListWidgetItem]) -> QMimeData:
        data = QMimeData()
        if items:
            data.setData(MIME_TYPE, str(items[0].data(KEY_ROLE)).encode("utf-8"))
            data.setText(items[0].text())
        return data


class LibraryPanel(QWidget):
    """Shows all templates (built-in and own) with a preview.

    Drag an entry into the 3D view to place it on the floor, or double-click
    to insert it next to the existing furniture.
    """

    insert_requested = Signal(str)  # template key

    def __init__(self, library: TemplateLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self._templates: dict[str, Template] = {}
        self._icons: dict[str, QIcon] = {}
        self.list = LibraryList()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.list.setGridSize(QSize(ICON_SIZE + 24, ICON_SIZE + 36))
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.list.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.list.itemDoubleClicked.connect(
            lambda item: self.insert_requested.emit(str(item.data(KEY_ROLE)))
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        self.reload()

    def reload(self) -> None:
        self._templates = {template_key(t): t for t in self.library.templates()}
        self.list.clear()
        for key, template in self._templates.items():
            if key not in self._icons:
                self._icons[key] = QIcon(render_preview(template))
            label = (
                template.name if template.builtin else self.tr("{0} (eigene)").format(template.name)
            )
            item = QListWidgetItem(self._icons[key], label)
            item.setData(KEY_ROLE, key)
            item.setToolTip(
                template.description
                + "\n\n"
                + self.tr("In die 3D-Ansicht ziehen oder doppelklicken zum Einfügen.")
            )
            self.list.addItem(item)

    def template(self, key: str) -> Template | None:
        return self._templates.get(key)
