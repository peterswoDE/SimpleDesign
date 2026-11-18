"""Input field for a number *or* a formula.

Layout: ``ƒ`` marker (only for formulas) · line edit · computed value.

* Enter / leaving the field submits the text (``submitted`` signal).
* Escape reverts to the stored formula.
* Input errors (syntax, unknown name) and evaluation errors (cycle …) turn
  the field red; the message is in the tooltip.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from simpledesign.core.params.expression import is_plain_number
from simpledesign.ui.texts import number

ERROR_STYLE = "QLineEdit { background: #fde2e2; border: 1px solid #d64545; }"


class ExpressionField(QWidget):
    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._error: str | None = None

        self.marker = QLabel("ƒ")
        self.marker.setToolTip(self.tr("Formel"))
        self.marker.setStyleSheet("color: #3b6fd6; font-style: italic; font-weight: bold;")
        self.marker.setFixedWidth(12)
        self.edit = QLineEdit()
        self.edit.installEventFilter(self)
        self.edit.editingFinished.connect(self._on_editing_finished)
        self.value_label = QLabel()
        self.value_label.setStyleSheet("color: #6b7785;")
        self.value_label.setMinimumWidth(56)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.marker)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.value_label)

    def set_state(self, text: str, value: float, error: str | None = None) -> None:
        """Show the stored formula, its value and an evaluation error (if any)."""
        self._text = text
        self._error = error
        if not self.edit.hasFocus() or not self.edit.isModified():
            self.edit.setText(text)
            self.edit.setModified(False)
        formula = not is_plain_number(text)
        self.marker.setVisible(formula)
        self.value_label.setText(f"= {number(value)}" if formula or error else "")
        self._show_error(error)

    def show_input_error(self, message: str) -> None:
        """The typed text was rejected: keep it so the user can fix it."""
        self._show_error(message)

    def _show_error(self, message: str | None) -> None:
        self.edit.setStyleSheet(ERROR_STYLE if message else "")
        self.edit.setToolTip(message or "")

    def _on_editing_finished(self) -> None:
        text = self.edit.text().strip()
        if not self.edit.isModified() or text == self._text:
            return
        self.edit.setModified(False)
        self.submitted.emit(text)

    def revert(self) -> None:
        self.edit.setText(self._text)
        self.edit.setModified(False)
        self._show_error(self._error)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.edit
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            self.revert()
            return True
        return super().eventFilter(watched, event)
