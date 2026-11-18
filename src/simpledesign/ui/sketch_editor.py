"""2D sketch editor: draw an outline or a cut-out, constrain and dimension it.

The editing logic lives in :class:`SketchSession` (Qt-free). This module
only draws and turns mouse/keyboard input into session calls.

Controls:

* Left click: select (Ctrl adds), drag points/lines, drag on empty space
  selects everything inside the rectangle.
* Drawing tools: click the points one after another; the line tool keeps
  going from the last point until Esc, a right click or closing the loop.
* Wheel: zoom at the cursor. Middle (or right) mouse drag: pan.
* Double click a dimension: edit its formula.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from simpledesign.app.controller import Controller
from simpledesign.app.sketch_session import (
    EditProblem,
    SketchEditError,
    SketchSession,
    Snap,
)
from simpledesign.core.ids import new_id
from simpledesign.core.model.part import Face, Orientation, SketchCut
from simpledesign.core.params.errors import FormulaError
from simpledesign.core.sketch.model import (
    ORIGIN,
    P2,
    Arc,
    Circle,
    Constraint,
    ConstraintKind,
    Line,
    Sketch,
)
from simpledesign.core.sketch.solver import References, Status
from simpledesign.ui.texts import error_message, number, orientation_label, profile_problem

K = ConstraintKind
PICK_PIXELS = 7.0

COLOR_LINE = QColor("#1f2933")
COLOR_CONSTRUCTION = QColor("#7b8794")
COLOR_SELECTED = QColor("#e8590c")
COLOR_HOVER = QColor("#f59f00")
COLOR_REFERENCE = QColor("#9fb3c8")
COLOR_DIMENSION = QColor("#2a6fdb")
COLOR_ERROR = QColor("#d64545")
COLOR_REGION = QColor(120, 160, 220, 40)
COLOR_PREVIEW = QColor("#2a6fdb")


def _tr(text: str) -> str:
    return QCoreApplication.translate("SketchEditor", text)


class Tool(StrEnum):
    SELECT = "select"
    LINE = "line"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    ARC = "arc"


def constraint_label(kind: ConstraintKind) -> str:
    labels = {
        K.COINCIDENT: _tr("Deckungsgleich"),
        K.ON: _tr("Auf Kontur"),
        K.HORIZONTAL: _tr("Horizontal"),
        K.VERTICAL: _tr("Vertikal"),
        K.PARALLEL: _tr("Parallel"),
        K.PERPENDICULAR: _tr("Senkrecht"),
        K.TANGENT: _tr("Tangential"),
        K.EQUAL: _tr("Gleich"),
        K.FIX: _tr("Fixiert"),
        K.DISTANCE: _tr("Abstand"),
        K.HDISTANCE: _tr("Horizontaler Abstand"),
        K.VDISTANCE: _tr("Vertikaler Abstand"),
        K.RADIUS: _tr("Radius"),
        K.ANGLE: _tr("Winkel"),
    }
    return labels[kind]


def edit_problem_text(error: SketchEditError) -> str:
    if error.problem == EditProblem.FORMULA and error.formula_error is not None:
        return error_message(error.formula_error)
    return {
        EditProblem.SELECTION: _tr("Diese Bedingung passt nicht zur Auswahl."),
        EditProblem.CONTRADICTION: _tr(
            "Das widerspricht den vorhandenen Bedingungen – nicht übernommen."
        ),
        EditProblem.REDUNDANT: _tr("Das ist bereits festgelegt – nicht übernommen."),
        EditProblem.FORMULA: _tr("Die Formel ist ungültig."),
    }[error.problem]


# ====================================================================== canvas
class SketchCanvas(QWidget):
    changed = Signal()  # sketch changed (re-read list and status)
    selection_changed = Signal()
    message = Signal(str)  # hint or error for the status line
    edit_dimension = Signal(str)  # constraint id

    def __init__(self, session: SketchSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.tool = Tool.SELECT
        self.construction = False
        self.scale = 2.0  # pixels per mm
        self.center: P2 = (0.0, 0.0)  # model point in the middle of the widget
        self.pending: list[Snap] = []  # clicked points of the current drawing tool
        self.mouse: P2 | None = None
        self.hover: str | None = None
        self._press: QPointF | None = None
        self._press_model: P2 = (0.0, 0.0)
        self._drag_id: str | None = None
        self._dragged = False
        self._pan_from: QPointF | None = None
        self._box: tuple[QPointF, QPointF] | None = None
        self.label_rects: dict[str, QRectF] = {}
        self._chain_start: str | None = None  # first point of the current line chain
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 360)

    # -------------------------------------------------------------- view
    def to_screen(self, p: P2) -> QPointF:
        return QPointF(
            self.width() / 2 + (p[0] - self.center[0]) * self.scale,
            self.height() / 2 - (p[1] - self.center[1]) * self.scale,
        )

    def to_model(self, p: QPointF) -> P2:
        return (
            self.center[0] + (p.x() - self.width() / 2) / self.scale,
            self.center[1] - (p.y() - self.height() / 2) / self.scale,
        )

    def tolerance(self) -> float:
        return PICK_PIXELS / self.scale

    def fit(self) -> None:
        pts = list(self.session.positions().values())
        for entity in self.session.sketch.entities.values():
            if isinstance(entity, Circle):
                c = self.session.positions()[entity.center]
                pts += [(c[0] - entity.radius, c[1] - entity.radius)]
                pts += [(c[0] + entity.radius, c[1] + entity.radius)]
        us = [p[0] for p in pts] or [0.0]
        vs = [p[1] for p in pts] or [0.0]
        w = max(max(us) - min(us), 50.0)
        h = max(max(vs) - min(vs), 50.0)
        self.center = ((max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2)
        width, height = max(self.width(), 100), max(self.height(), 100)
        self.scale = min(width / (w * 1.25), height / (h * 1.25))
        self.update()

    def set_tool(self, tool: Tool) -> None:
        self.tool = tool
        self.pending.clear()
        self._chain_start = None
        self.setCursor(
            Qt.CursorShape.ArrowCursor if tool == Tool.SELECT else Qt.CursorShape.CrossCursor
        )
        self.message.emit(self.hint())
        self.update()

    def hint(self) -> str:
        n = len(self.pending)
        hints = {
            Tool.SELECT: self.tr(
                "Klicken wählt aus (Strg: dazu), Ziehen verschiebt, Rahmen ziehen wählt mehrere."
            ),
            Tool.LINE: self.tr("Startpunkt klicken")
            if n == 0
            else self.tr("Nächsten Punkt klicken – Esc oder Rechtsklick beendet"),
            Tool.RECTANGLE: self.tr("Erste Ecke klicken")
            if n == 0
            else self.tr("Gegenüberliegende Ecke klicken"),
            Tool.CIRCLE: self.tr("Mittelpunkt klicken") if n == 0 else self.tr("Radius klicken"),
            Tool.ARC: (
                self.tr("Mittelpunkt klicken"),
                self.tr("Anfangspunkt klicken"),
                self.tr("Endpunkt klicken (gegen den Uhrzeigersinn)"),
            )[min(n, 2)],
        }
        return hints[self.tool]

    # -------------------------------------------------------------- input
    def wheelEvent(self, event: QWheelEvent) -> None:
        before = self.to_model(event.position())
        factor = 1.0015 ** event.angleDelta().y()
        self.scale = min(max(self.scale * factor, 0.01), 200.0)
        after = self.to_model(event.position())
        self.center = (
            self.center[0] + before[0] - after[0],
            self.center[1] + before[1] - after[1],
        )
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        model = self.to_model(pos)
        button = event.button()
        if button == Qt.MouseButton.MiddleButton or (
            button == Qt.MouseButton.RightButton and not self.pending
        ):
            self._pan_from = pos
            return
        if button == Qt.MouseButton.RightButton:
            self.cancel()
            return
        if button != Qt.MouseButton.LeftButton:
            return
        self._press = pos
        self._press_model = model
        if self.tool == Tool.SELECT:
            self._select_press(model, event.modifiers())
        else:
            self._draw_click(model, event.modifiers())
        self.update()

    def _select_press(self, model: P2, modifiers: Qt.KeyboardModifier) -> None:
        session = self.session
        hit = self._label_at(self._press) if self._press is not None else None
        hit = hit or session.pick(model, self.tolerance())
        add = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if hit is None:
            if not add:
                session.selection = []
            self._box = (self._press, self._press) if self._press is not None else None
        elif add:
            if hit in session.selection:
                session.selection.remove(hit)
            else:
                session.selection.append(hit)
        else:
            if hit not in session.selection:
                session.selection = [hit]
            movable = hit in session.sketch.points or hit in session.sketch.entities
            self._drag_id = hit if movable else None
            self._dragged = False
        self.selection_changed.emit()

    def _draw_click(self, model: P2, modifiers: Qt.KeyboardModifier) -> None:
        session = self.session
        free = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        snap = Snap(model) if free else session.snap(model, self.tolerance())
        self.pending.append(snap)
        made = False
        if self.tool == Tool.LINE and len(self.pending) == 2:
            first, second = self.pending
            line = session.add_line(first, second, self.construction)
            self.pending = []
            if line is not None:
                made = True
                pos = session.positions()
                if self._chain_start is None:
                    self._chain_start = line.start
                # Keep drawing from the end point unless the loop was closed.
                if math.dist(pos[line.end], pos[self._chain_start]) < 1e-6:
                    self._chain_start = None
                else:
                    self.pending = [Snap(pos[line.end], line.end)]
        elif self.tool == Tool.RECTANGLE and len(self.pending) == 2:
            made = bool(session.add_rectangle(*self.pending))
            self.pending = []
        elif self.tool == Tool.CIRCLE and len(self.pending) == 2:
            center, rim = self.pending
            radius = math.dist(center.pos, rim.pos)
            made = session.add_circle(center, radius, self.construction) is not None
            self.pending = []
        elif self.tool == Tool.ARC and len(self.pending) == 3:
            center, start, finish = self.pending
            made = session.add_arc(center, start, finish.pos) is not None
            self.pending = []
        if made:
            self.changed.emit()
        self.message.emit(self.hint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        model = self.to_model(pos)
        self.mouse = model
        if self._pan_from is not None:
            delta = pos - self._pan_from
            self._pan_from = pos
            self.center = (
                self.center[0] - delta.x() / self.scale,
                self.center[1] + delta.y() / self.scale,
            )
        elif self._box is not None:
            self._box = (self._box[0], pos)
        elif self._drag_id is not None and self._press is not None:
            if not self._dragged:
                if (pos - self._press).manhattanLength() < 3:
                    return
                self.session.checkpoint()
                self._dragged = True
            session = self.session
            if self._drag_id in session.sketch.points:
                session.drag(self._drag_id, model)
            else:
                last = self._press_model
                session.move_entity(self._drag_id, (model[0] - last[0], model[1] - last[1]))
                self._press_model = model
            self.changed.emit()
        else:
            hover = self.session.pick(model, self.tolerance())
            if self.tool != Tool.SELECT:
                hover = self.session.nearest_point(model, self.tolerance())
            self.hover = hover
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._pan_from = None
        if self._box is not None:
            a, b = (self.to_model(p) for p in self._box)
            self._select_box(a, b, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            self._box = None
        if self._drag_id is not None and self._dragged:
            self.session.solve()
            self.changed.emit()
        self._drag_id = None
        self._dragged = False
        self._press = None
        self.update()

    def _select_box(self, a: P2, b: P2, add: bool) -> None:
        lo = (min(a[0], b[0]), min(a[1], b[1]))
        hi = (max(a[0], b[0]), max(a[1], b[1]))
        pos = self.session.positions()

        def inside(p: str) -> bool:
            u, v = pos[p]
            return lo[0] <= u <= hi[0] and lo[1] <= v <= hi[1]

        sketch = self.session.sketch
        found = [e for e in sketch.entities if all(inside(p) for p in sketch.point_ids_of(e))]
        found += [p for p in sketch.points if inside(p)]
        selection = list(self.session.selection) if add else []
        selection += [f for f in found if f not in selection]
        self.session.selection = selection
        self.selection_changed.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        label = self._label_at(event.position())
        if label is not None:
            self.edit_dimension.emit(label)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.session.delete_selection():
                self.changed.emit()
                self.selection_changed.emit()
        else:
            super().keyPressEvent(event)

    def cancel(self) -> None:
        """Esc: stop drawing, then clear the selection, then back to selecting."""
        if self.pending:
            self.pending = []
        elif self.session.selection:
            self.session.selection = []
            self.selection_changed.emit()
        else:
            self.set_tool(Tool.SELECT)
        self._chain_start = None
        self.message.emit(self.hint())
        self.update()

    def _label_at(self, pos: QPointF) -> str | None:
        for cid, rect in self.label_rects.items():
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return cid
        return None

    # -------------------------------------------------------------- painting
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        self._paint_grid(painter)
        self._paint_regions(painter)
        self._paint_references(painter)
        self._paint_entities(painter)
        self._paint_constraints(painter)
        self._paint_preview(painter)
        if self._box is not None:
            painter.setPen(QPen(COLOR_DIMENSION, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(42, 111, 219, 25))
            painter.drawRect(QRectF(*self._box).normalized())
        painter.end()

    def _paint_grid(self, painter: QPainter) -> None:
        step = 10 ** math.floor(math.log10(max(20 / self.scale, 1e-6)))
        if step * self.scale < 8:
            step *= 5
        lo = self.to_model(QPointF(0, self.height()))
        hi = self.to_model(QPointF(self.width(), 0))
        painter.setPen(QPen(QColor("#eef2f6"), 1))
        u = math.floor(lo[0] / step) * step
        while u <= hi[0]:
            x = self.to_screen((u, 0)).x()
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            u += step
        v = math.floor(lo[1] / step) * step
        while v <= hi[1]:
            y = self.to_screen((0, v)).y()
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
            v += step

    def _paint_regions(self, painter: QPainter) -> None:
        from simpledesign.core.sketch import profile

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for region in self.session.regions:
            for loop in (region.outer, *region.holes):
                pts = [self.to_screen(p) for p in profile.polygon(loop)]
                if pts:
                    path.moveTo(pts[0])
                    for p in pts[1:]:
                        path.lineTo(p)
                    path.closeSubpath()
        painter.fillPath(path, COLOR_REGION)

    def _paint_references(self, painter: QPainter) -> None:
        refs = self.session.references
        pos = self.session.positions()
        for line_id, (a, b) in refs.lines.items():
            color = self._color(line_id, COLOR_REFERENCE)
            painter.setPen(QPen(color, 1.5 if line_id.startswith("e") else 1, Qt.PenStyle.DashLine))
            pa, pb = self.to_screen(pos[a]), self.to_screen(pos[b])
            if line_id in ("ox", "oy"):  # axes: draw across the whole view
                d = pb - pa
                length = math.hypot(d.x(), d.y()) or 1.0
                d = d * (4000 / length)
                pa, pb = pa - d, pb + d
            painter.drawLine(pa, pb)
        for point_id in refs.points:
            if point_id in ("ux", "vy"):
                continue
            self._paint_point(painter, pos[point_id], self._color(point_id, COLOR_REFERENCE))

    def _color(self, obj_id: str, normal: QColor) -> QColor:
        if obj_id in self.session.selection:
            return COLOR_SELECTED
        if obj_id == self.hover:
            return COLOR_HOVER
        return normal

    def _paint_entities(self, painter: QPainter) -> None:
        session = self.session
        pos = session.positions()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for entity in session.sketch.entities.values():
            normal = COLOR_CONSTRUCTION if entity.construction else COLOR_LINE
            style = Qt.PenStyle.DashLine if entity.construction else Qt.PenStyle.SolidLine
            painter.setPen(QPen(self._color(entity.id, normal), 2, style))
            if isinstance(entity, Line):
                painter.drawLine(self.to_screen(pos[entity.start]), self.to_screen(pos[entity.end]))
            elif isinstance(entity, Circle):
                c = self.to_screen(pos[entity.center])
                r = entity.radius * self.scale
                painter.drawEllipse(c, r, r)
            elif isinstance(entity, Arc):
                center, radius = session.center_radius(entity.id)
                s, e = pos[entity.start], pos[entity.end]
                t0 = math.degrees(math.atan2(s[1] - center[1], s[0] - center[0]))
                t1 = math.degrees(math.atan2(e[1] - center[1], e[0] - center[0]))
                sweep = (t1 - t0) % 360 or 360
                c = self.to_screen(center)
                r = radius * self.scale
                rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
                painter.drawArc(rect, round(t0 * 16), round(sweep * 16))
        for point_id, point in session.sketch.points.items():
            self._paint_point(painter, point.pos, self._color(point_id, COLOR_LINE))

    def _paint_point(self, painter: QPainter, p: P2, color: QColor) -> None:
        s = self.to_screen(p)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRect(QRectF(s.x() - 2.5, s.y() - 2.5, 5, 5))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_constraints(self, painter: QPainter) -> None:
        session = self.session
        failed = set(session.result.failed) | set(session.formula_errors)
        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF() - 1, 7))
        painter.setFont(font)
        self.label_rects = {}
        for c in session.sketch.constraints.values():
            anchor = self._anchor(c)
            if anchor is None:
                continue
            p = self.to_screen(anchor)
            if c.is_dimension:
                text = self._dimension_text(c)
                color = COLOR_ERROR if c.id in failed else COLOR_DIMENSION
            elif c.kind in (K.HORIZONTAL, K.VERTICAL):
                text = "H" if c.kind == K.HORIZONTAL else "V"
                color = COLOR_CONSTRUCTION
            elif c.kind in (K.PARALLEL, K.PERPENDICULAR, K.TANGENT, K.EQUAL, K.FIX):
                text = {
                    K.PARALLEL: "∥",
                    K.PERPENDICULAR: "⊥",
                    K.TANGENT: "T",
                    K.EQUAL: "=",
                    K.FIX: "⚓",
                }[c.kind]
                color = COLOR_CONSTRUCTION
            else:
                continue
            if c.id in session.selection:
                color = COLOR_SELECTED
            elif c.id in failed:
                color = COLOR_ERROR
            metrics = painter.fontMetrics()
            w = metrics.horizontalAdvance(text) + 6
            h = metrics.height() + 2
            rect = QRectF(p.x() - w / 2, p.y() - h / 2, w, h)
            if c.is_dimension:
                painter.setPen(QPen(color, 1))
                painter.setBrush(QColor(255, 255, 255, 220))
                painter.drawRoundedRect(rect, 3, 3)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            self.label_rects[c.id] = rect

    def _dimension_text(self, c: Constraint) -> str:
        formula = self.session.formulas.get(c.id, "")
        value = self.session.measure(c)
        prefix = {K.RADIUS: "R ", K.ANGLE: "∠ "}.get(c.kind, "")
        shown = number(abs(value)) + ("°" if c.kind == K.ANGLE else "")
        try:
            float(formula.replace(",", "."))
            return prefix + shown
        except ValueError:
            return f"{prefix}{formula} = {shown}"

    def _anchor(self, c: Constraint) -> P2 | None:
        """Model position of a constraint's label."""
        session = self.session
        pos = session.positions()
        refs = c.refs
        offset = 12 / self.scale
        try:
            if c.kind == K.RADIUS:
                center, radius = session.center_radius(refs[0])
                return (center[0] + radius * 0.7071, center[1] + radius * 0.7071)
            if c.kind == K.ANGLE:
                points = [pos[p] for line in refs for p in session.line_points(line)]
                return (
                    sum(p[0] for p in points) / 4,
                    sum(p[1] for p in points) / 4,
                )
            if c.kind == K.FIX:
                p = pos[refs[0]]
                return (p[0] + offset, p[1] + offset)
            if len(refs) == 1 and session.is_line(refs[0]):
                a, b = (pos[p] for p in session.line_points(refs[0]))
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = math.hypot(dx, dy) or 1.0
                side = 1.0 if c.is_dimension else -1.0
                return (
                    (a[0] + b[0]) / 2 - dy / length * offset * side,
                    (a[1] + b[1]) / 2 + dx / length * offset * side,
                )
            if c.kind in (K.PARALLEL, K.PERPENDICULAR, K.EQUAL, K.TANGENT):
                first = refs[0]
                if session.is_line(first):
                    a, b = (pos[p] for p in session.line_points(first))
                    return ((a[0] + b[0]) / 2 + offset, (a[1] + b[1]) / 2 + offset)
                center, radius = session.center_radius(first)
                return (center[0], center[1] + radius + offset)
            points = [pos[r] for r in refs if r in pos]
            if c.kind == K.DISTANCE and len(points) == 1:  # point - line
                a, b = (pos[p] for p in session.line_points(refs[1]))
                points.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            if len(points) == 2:
                return ((points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2)
        except KeyError, AssertionError:
            return None
        return None

    def _paint_preview(self, painter: QPainter) -> None:
        if self.mouse is None:
            return
        if self.hover is not None and self.tool != Tool.SELECT:
            p = self.to_screen(self.session.positions()[self.hover])
            painter.setPen(QPen(COLOR_HOVER, 2))
            painter.drawEllipse(p, 6, 6)
        if not self.pending:
            return
        painter.setPen(QPen(COLOR_PREVIEW, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        mouse = self.session.snap(self.mouse, self.tolerance()).pos
        first = self.pending[0].pos
        a, m = self.to_screen(first), self.to_screen(mouse)
        if self.tool == Tool.LINE:
            painter.drawLine(a, m)
            self._preview_label(painter, m, number(math.dist(first, mouse)))
        elif self.tool == Tool.RECTANGLE:
            painter.drawRect(QRectF(a, m).normalized())
            w, h = abs(mouse[0] - first[0]), abs(mouse[1] - first[1])
            self._preview_label(painter, m, f"{number(w)} × {number(h)}")
        elif self.tool == Tool.CIRCLE:
            r = math.dist(first, mouse)
            painter.drawEllipse(a, r * self.scale, r * self.scale)
            self._preview_label(painter, m, "R " + number(r))
        elif self.tool == Tool.ARC:
            r = math.dist(first, self.pending[1].pos if len(self.pending) > 1 else mouse)
            painter.drawEllipse(a, r * self.scale, r * self.scale)
            self._preview_label(painter, m, "R " + number(r))

    def _preview_label(self, painter: QPainter, at: QPointF, text: str) -> None:
        painter.setPen(COLOR_PREVIEW)
        painter.drawText(at + QPointF(12, -8), text)


# ====================================================================== dialog
class SketchEditor(QDialog):
    """Edit a sketch. Read the result from ``sketch`` / ``formulas`` after accept."""

    def __init__(
        self,
        sketch: Sketch,
        formulas: dict[str, str],
        references: References,
        value_of: Callable[[str], float],
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1200, 780)
        self.session = SketchSession(sketch, formulas, references, value_of)
        self.canvas = SketchCanvas(self.session)
        self._filling = False

        self.toolbar = QToolBar()
        self.tool_actions: dict[Tool, QAction] = {}
        group = QActionGroup(self)
        for tool, label, key in (
            (Tool.SELECT, self.tr("Auswahl"), "S"),
            (Tool.LINE, self.tr("Linie"), "L"),
            (Tool.RECTANGLE, self.tr("Rechteck"), "R"),
            (Tool.CIRCLE, self.tr("Kreis"), "C"),
            (Tool.ARC, self.tr("Bogen"), "A"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(key))
            action.setToolTip(f"{label} ({key})")
            action.triggered.connect(lambda _c=False, t=tool: self.set_tool(t))
            group.addAction(action)
            self.toolbar.addAction(action)
            self.tool_actions[tool] = action
        self.tool_actions[Tool.SELECT].setChecked(True)
        self.construction_action = QAction(self.tr("Hilfsgeometrie"), self)
        self.construction_action.setToolTip(
            self.tr("Neue Linien/Kreise als Hilfsgeometrie zeichnen bzw. Auswahl umschalten (X)")
        )
        self.construction_action.setShortcut(QKeySequence("X"))
        self.construction_action.triggered.connect(self._on_construction)
        self.toolbar.addAction(self.construction_action)
        self.toolbar.addSeparator()

        self.constraint_actions: dict[str, QAction] = {}
        for key, kind, label, shortcut in (
            ("dimension", None, self.tr("Bemaßen"), "D"),
            ("hdistance", K.HDISTANCE, self.tr("Horiz. Maß"), "Shift+H"),
            ("vdistance", K.VDISTANCE, self.tr("Vert. Maß"), "Shift+V"),
            ("angle", K.ANGLE, self.tr("Winkel"), ""),
            ("horizontal", K.HORIZONTAL, self.tr("Horizontal"), "H"),
            ("vertical", K.VERTICAL, self.tr("Vertikal"), "V"),
            ("coincident", K.COINCIDENT, self.tr("Deckungsgleich"), "O"),
            ("parallel", K.PARALLEL, self.tr("Parallel"), "P"),
            ("perpendicular", K.PERPENDICULAR, self.tr("Senkrecht"), "Shift+P"),
            ("tangent", K.TANGENT, self.tr("Tangential"), "T"),
            ("equal", K.EQUAL, self.tr("Gleich"), "E"),
            ("fix", K.FIX, self.tr("Fixieren"), "F"),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setToolTip(f"{label} ({shortcut})")
            action.triggered.connect(lambda _c=False, k=kind: self.constrain(k))
            self.toolbar.addAction(action)
            self.constraint_actions[key] = action
        self.toolbar.addSeparator()
        self.delete_action = QAction(self.tr("Löschen"), self)
        self.delete_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        self.delete_action.triggered.connect(self.delete_selection)
        self.undo_action = QAction(self.tr("Rückgängig"), self)
        self.undo_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Undo))
        self.undo_action.triggered.connect(self.undo)
        self.redo_action = QAction(self.tr("Wiederholen"), self)
        self.redo_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Redo))
        self.redo_action.triggered.connect(self.redo)
        self.fit_action = QAction(self.tr("Einpassen"), self)
        self.fit_action.setShortcut(QKeySequence("F6"))
        self.fit_action.triggered.connect(self.canvas.fit)
        self.toolbar.addActions(
            [self.delete_action, self.undo_action, self.redo_action, self.fit_action]
        )

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr("Bedingung"), self.tr("Formel"), "="])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_table_selection)

        self.options = QWidget()
        self.options_layout = QFormLayout(self.options)
        self.options_layout.setContentsMargins(0, 0, 0, 0)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.options)
        box = QGroupBox(self.tr("Bedingungen und Maße"))
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(self.table)
        side_layout.addWidget(box, 1)

        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.hint = QLabel()
        self.hint.setStyleSheet("color: #52606d;")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        bottom = QHBoxLayout()
        texts = QVBoxLayout()
        texts.addWidget(self.status)
        texts.addWidget(self.hint)
        bottom.addLayout(texts, 1)
        bottom.addWidget(self.buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.toolbar)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

        self.canvas.changed.connect(self._on_changed)
        self.canvas.selection_changed.connect(self._sync_table_selection)
        self.canvas.message.connect(self.hint.setText)
        self.canvas.edit_dimension.connect(self.edit_formula)
        self._on_changed()
        self.hint.setText(self.canvas.hint())

    # -------------------------------------------------------------- results
    @property
    def sketch(self) -> Sketch:
        return self.session.sketch

    @property
    def formulas(self) -> dict[str, str]:
        return dict(self.session.formulas)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.canvas.fit()

    def accept(self) -> None:
        if self.session.valid or self._accept_anyway():
            super().accept()

    def _accept_anyway(self) -> bool:
        return False

    # -------------------------------------------------------------- actions
    def set_tool(self, tool: Tool) -> None:
        self.tool_actions[tool].setChecked(True)
        self.canvas.set_tool(tool)
        self.canvas.setFocus()

    def constrain(self, kind: ConstraintKind | None) -> bool:
        try:
            self.session.constrain(kind)
        except SketchEditError as error:
            self._show_error(edit_problem_text(error))
            return False
        self._on_changed()
        self._sync_table_selection()
        return True

    def delete_selection(self) -> None:
        if self.session.delete_selection():
            self._on_changed()

    def undo(self) -> None:
        self.session.undo()
        self._on_changed()

    def redo(self) -> None:
        self.session.redo()
        self._on_changed()

    def _on_construction(self) -> None:
        if self.session.toggle_construction():
            self._on_changed()
        else:
            self.canvas.construction = not self.canvas.construction
            self.construction_action.setCheckable(True)
            self.construction_action.setChecked(self.canvas.construction)

    def edit_formula(self, constraint_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == constraint_id:
                self.table.setCurrentItem(item)
                self.table.editItem(item)
                return

    def set_formula(self, constraint_id: str, text: str) -> bool:
        try:
            self.session.set_formula(constraint_id, text)
        except SketchEditError as error:
            self._show_error(edit_problem_text(error))
            self._fill_table()
            return False
        self._on_changed()
        return True

    # -------------------------------------------------------------- refresh
    def _on_changed(self) -> None:
        self._fill_table()
        self._update_status()
        self.undo_action.setEnabled(self.session.can_undo())
        self.redo_action.setEnabled(self.session.can_redo())
        self.canvas.update()

    def _show_error(self, text: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {COLOR_ERROR.name()};")

    def status_text(self) -> tuple[str, bool]:
        session = self.session
        result = session.result
        if session.formula_errors:
            first = next(iter(session.formula_errors.values()))
            return self.tr("Formelfehler: {msg}").format(msg=error_message(first)), False
        if result.status == Status.FAILED:
            return self.tr("Widersprüchliche Bedingungen (rot markiert)."), False
        if session.profile_problem is not None:
            text = self.tr("Keine geschlossene Kontur: {problem}.")
            return text.format(problem=profile_problem(session.profile_problem.value)), False
        if result.status == Status.UNDERDETERMINED:
            text = self.tr("Kontur geschlossen – noch {n} Freiheitsgrade (Maße ergänzen).")
            return text.format(n=result.dof), True
        if result.status == Status.REDUNDANT:
            return self.tr("Kontur geschlossen – einige Bedingungen sind doppelt."), True
        return self.tr("Vollständig bestimmt."), True

    def _update_status(self) -> None:
        text, ok = self.status_text()
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {'#2b8a3e' if ok else COLOR_ERROR.name()};")
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        button.setEnabled(self.session.valid)

    def _fill_table(self) -> None:
        self._filling = True
        try:
            session = self.session
            constraints = list(session.sketch.constraints.values())
            constraints.sort(key=lambda c: (not c.is_dimension, c.kind.value))
            failed = set(session.result.failed) | set(session.formula_errors)
            self.table.setRowCount(len(constraints))
            for row, c in enumerate(constraints):
                kind_item = QTableWidgetItem(constraint_label(c.kind))
                kind_item.setFlags(kind_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                kind_item.setData(Qt.ItemDataRole.UserRole, c.id)
                formula_item = QTableWidgetItem(session.formulas.get(c.id, ""))
                formula_item.setData(Qt.ItemDataRole.UserRole, c.id)
                value_item = QTableWidgetItem()
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c.is_dimension:
                    value = abs(session.measure(c))
                    value_item.setText(number(value) + ("°" if c.kind == K.ANGLE else ""))
                else:
                    formula_item.setFlags(formula_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c.id in failed:
                    for item in (kind_item, formula_item, value_item):
                        item.setForeground(COLOR_ERROR)
                    if c.id in session.formula_errors:
                        formula_item.setToolTip(error_message(session.formula_errors[c.id]))
                self.table.setItem(row, 0, kind_item)
                self.table.setItem(row, 1, formula_item)
                self.table.setItem(row, 2, value_item)
            self.table.resizeColumnToContents(0)
            self._sync_table_selection()
        finally:
            self._filling = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
            return
        constraint_id = str(item.data(Qt.ItemDataRole.UserRole))
        text = item.text().strip()
        if text == self.session.formulas.get(constraint_id):
            return
        self.set_formula(constraint_id, text)

    def _on_table_selection(self) -> None:
        if self._filling:
            return
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
        if ids or any(s in self.session.sketch.constraints for s in self.session.selection):
            self.session.selection = ids
            self.canvas.update()

    def _sync_table_selection(self) -> None:
        was = self._filling
        self._filling = True
        try:
            self.table.clearSelection()
            selected = set(self.session.selection)
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) in selected:
                    self.table.selectRow(row)
        finally:
            self._filling = was
        self.canvas.update()


# ====================================================================== variants
class NewPartEditor(SketchEditor):
    """Sketch for a new part: also asks in which plane it lies."""

    PLANES = (Orientation.XZ, Orientation.XY, Orientation.YZ)

    def __init__(self, value_of: Callable[[str], float], parent: QWidget | None = None) -> None:
        super().__init__(
            Sketch(), {}, References.for_plane(), value_of, self.tr("Skizzenteil"), parent
        )
        self.plane_combo = QComboBox()
        for orientation in self.PLANES:
            self.plane_combo.addItem(orientation_label(orientation), orientation.value)
        self.options_layout.addRow(self.tr("Lage"), self.plane_combo)
        self.set_tool(Tool.LINE)

    @property
    def orientation(self) -> Orientation:
        return Orientation(str(self.plane_combo.currentData()))


class CutEditor(SketchEditor):
    """Cut-out (pocket or through hole) on a face of a board."""

    def __init__(
        self,
        sketch: Sketch,
        formulas: dict[str, str],
        references: References,
        value_of: Callable[[str], float],
        face: Face = Face.TOP,
        through: bool = False,
        depth: str = "5",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(sketch, formulas, references, value_of, self.tr("Ausschnitt"), parent)
        self.face_combo = QComboBox()
        self.face_combo.addItem(self.tr("Oberseite"), Face.TOP.value)
        self.face_combo.addItem(self.tr("Unterseite"), Face.BOTTOM.value)
        self.face_combo.setCurrentIndex(self.face_combo.findData(face.value))
        self.through_check = QCheckBox(self.tr("Durchgehend"))
        self.through_check.setChecked(through)
        self.depth_edit = QLineEdit(depth)
        self.depth_edit.setToolTip(self.tr("Tiefe in mm – Zahl oder Formel"))
        self.depth_edit.textChanged.connect(self._update_status)
        self.through_check.toggled.connect(self._on_through)
        self.options_layout.addRow(self.tr("Seite"), self.face_combo)
        self.options_layout.addRow(self.tr("Tiefe"), self.depth_edit)
        self.options_layout.addRow("", self.through_check)
        self._on_through(through)
        if not sketch.entities:
            self.set_tool(Tool.RECTANGLE)

    def _on_through(self, checked: bool) -> None:
        self.depth_edit.setEnabled(not checked)
        self._update_status()

    @property
    def face(self) -> Face:
        return Face(str(self.face_combo.currentData()))

    @property
    def through(self) -> bool:
        return self.through_check.isChecked()

    @property
    def depth_text(self) -> str:
        return self.depth_edit.text().strip() or "0"

    def _update_status(self) -> None:
        super()._update_status()
        if not hasattr(self, "depth_edit") or self.through:
            return
        try:
            depth = self.session.value_of(self.depth_text)
            ok = depth > 0
            message = None if ok else self.tr("Die Tiefe muss größer als 0 sein.")
        except FormulaError as error:
            ok, message = False, error_message(error)
        self.depth_edit.setStyleSheet("" if ok else "QLineEdit { background: #fde2e2; }")
        self.depth_edit.setToolTip(message or self.tr("Tiefe in mm – Zahl oder Formel"))
        if not ok:
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)


# ====================================================================== launchers
# Each opens the (non-modal-blocking) dialog with open() and applies the result
# on accept. The dialog is returned so callers and tests can drive it.


def _apply(editor: SketchEditor, action: Callable[[], None]) -> None:
    def on_accepted() -> None:
        try:
            action()
        except FormulaError as error:
            editor.setResult(QDialog.DialogCode.Rejected)
            parent = editor.parentWidget()
            if parent is not None and hasattr(parent, "statusBar"):
                parent.statusBar().showMessage(error_message(error), 6000)

    editor.accepted.connect(on_accepted)
    editor.open()


def new_sketch_part(parent: QWidget | None, controller: Controller) -> NewPartEditor:
    editor = NewPartEditor(lambda text: controller.formula_value(None, text), parent)

    def apply() -> None:
        controller.add_profile_board(editor.orientation, editor.sketch, editor.formulas)

    _apply(editor, apply)
    return editor


def edit_profile(parent: QWidget | None, controller: Controller, board_id: str) -> SketchEditor:
    board = controller.doc.boards[board_id]
    if board.profile is not None:
        sketch = board.profile
        formulas = controller.sketch_formulas(board_id, sketch)
    else:  # start with the current rectangle, fully dimensioned
        sketch, formulas = _rectangle_sketch(
            controller.evaluation.value(board_id, "length"),
            controller.evaluation.value(board_id, "width"),
            controller.expression_text(board_id, "length"),
            controller.expression_text(board_id, "width"),
        )
    editor = SketchEditor(
        sketch,
        formulas,
        References.for_plane(),
        lambda text: controller.formula_value(board_id, text),
        _tr("Kontur von {name}").format(name=board.name),
        parent,
    )
    _apply(editor, lambda: controller.set_profile(board_id, editor.sketch, editor.formulas))
    return editor


def edit_cut(
    parent: QWidget | None, controller: Controller, board_id: str, cut_id: str | None = None
) -> CutEditor:
    """Edit an existing cut-out, or draw a new one (``cut_id`` None)."""
    board = controller.doc.boards[board_id]
    length = controller.evaluation.value(board_id, "length")
    width = controller.evaluation.value(board_id, "width")
    existing = next((c for c in board.cuts if c.id == cut_id), None)
    if existing is not None:
        cut = existing
        formulas = controller.sketch_formulas(board_id, cut.sketch)
        depth = controller.expression_text(board_id, f"depth.{cut.id}")
    else:
        cut = SketchCut(new_id(), Sketch())
        formulas, depth = {}, "5"
    editor = CutEditor(
        cut.sketch,
        formulas,
        References.for_face(length, width),
        lambda text: controller.formula_value(board_id, text),
        cut.face,
        cut.through,
        depth,
        parent,
    )

    def apply() -> None:
        result = SketchCut(cut.id, editor.sketch, editor.face, editor.through)
        controller.set_cut(board_id, result, editor.formulas, editor.depth_text)

    _apply(editor, apply)
    return editor


def _rectangle_sketch(
    length: float, width: float, length_text: str, width_text: str
) -> tuple[Sketch, dict[str, str]]:
    sketch = Sketch()
    lines = sketch.add_rectangle((0.0, 0.0), (length, width))
    sketch.add_constraint(K.COINCIDENT, (lines[0].start, ORIGIN))
    w = sketch.add_constraint(K.DISTANCE, (lines[0].id,))
    h = sketch.add_constraint(K.DISTANCE, (lines[1].id,))
    return sketch, {w.id: length_text or number(length), h.id: width_text or number(width)}
