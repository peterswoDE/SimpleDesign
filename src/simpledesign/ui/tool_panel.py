"""Floating panel of the active click tool: title, hint and the tool's options.

It stays next to the 3D view while a tool runs (doors, drawers, fitting a
board, measuring …) and does not take the keyboard from the view, so the
tool's keys keep working.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simpledesign.ui.scene import Scene

MARGIN = 12


class ToolPanel(QDialog):
    def __init__(self, scene: Scene, anchor: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scene = scene
        self.anchor = anchor  # the 3D view: the panel sits at its top left
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setModal(False)
        self.title = QLabel()
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setMinimumWidth(260)
        self.hint.setMaximumWidth(340)
        self.hint.setStyleSheet("color: palette(placeholder-text)")
        self.options_box = QVBoxLayout()
        self.options_box.setContentsMargins(0, 0, 0, 0)
        self.done_button = QPushButton(self.tr("Fertig"))
        self.done_button.setToolTip(self.tr("Werkzeug beenden (Esc)"))
        self.done_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.done_button.clicked.connect(lambda: self.scene.end_tool())
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.done_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addLayout(self.options_box)
        layout.addLayout(buttons)
        self._options: QWidget | None = None
        self._placed = False
        scene.tool_changed.connect(self.update_tool)
        scene.status_message.connect(self.set_hint)

    def set_hint(self, text: str) -> None:
        if self.scene.tool is not None:
            self.hint.setText(text)

    def update_tool(self) -> None:
        tool = self.scene.tool
        if self._options is not None:
            self.options_box.removeWidget(self._options)
            self._options.deleteLater()
            self._options = None
        if tool is None:
            self.hide()
            return
        self.setWindowTitle(tool.title())
        self.title.setText(tool.title())
        self._options = tool.options_widget()
        if self._options is not None:
            self.options_box.addWidget(self._options)
        self.adjustSize()
        if not self._placed:
            self.move(self.anchor.mapToGlobal(QPoint(MARGIN, MARGIN)))
            self._placed = True  # afterwards it stays where the user put it
        self.show()
        self.anchor.setFocus()

    def reject(self) -> None:  # Esc or the close button: end the tool
        self.scene.end_tool()
