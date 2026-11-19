"""Tool buttons show their keyboard shortcut in brackets: "Messen (M)".

The label follows when the shortcut is changed later (shortcuts dialog).
Menus are unaffected: they use the action text and show shortcuts themselves.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from shiboken6 import isValid

_BASE = "_shortcut_label_base"
_TIP = "_shortcut_label_tip"

# Qt's English modifier names in the German UI
_GERMAN = (("Ctrl", "Strg"), ("Shift", "Umschalt"), ("Del", "Entf"))


def shortcut_text(sequence: QKeySequence) -> str:
    text = sequence.toString(QKeySequence.SequenceFormat.NativeText)
    for english, german in _GERMAN:
        text = text.replace(english, german)
    return text


def show_shortcut(action: QAction) -> None:
    """Append the shortcut to the action's button label (and own tooltip)."""
    if action.property(_BASE) is not None:
        return
    base = action.iconText()
    tip = action.toolTip()
    action.setProperty(_BASE, base)
    action.setProperty(_TIP, tip if tip != base else "")
    _update(action)
    action.changed.connect(lambda: _update(action))


def _update(action: QAction) -> None:
    if not isValid(action):  # signal while the window is torn down
        return
    base = str(action.property(_BASE))
    tip = str(action.property(_TIP))
    keys = shortcut_text(action.shortcut())
    label = f"{base} ({keys})" if keys else base
    if action.iconText() != label:  # setting it emits changed again
        action.setIconText(label)
    if tip:
        full_tip = f"{tip} ({keys})" if keys else tip
        if action.toolTip() != full_tip:
            action.setToolTip(full_tip)
