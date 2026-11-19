from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtWidgets import QColorDialog, QFileDialog, QMessageBox, QWidget

# Test windows open far outside the desktop and never take the focus, so a test
# run does not get in the way at the PC. SIMPLEDESIGN_SHOW_TESTS=1 shows them.
# (QT_QPA_PLATFORM=offscreen is no option: VTK then renders into a 0x0 window.)
OFF_DESKTOP = (-32000, -32000)
_show = QWidget.show


def _show_off_desktop(widget: QWidget) -> None:
    if widget.isWindow():
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        widget.move(*OFF_DESKTOP)
    _show(widget)


if not os.environ.get("SIMPLEDESIGN_SHOW_TESTS"):
    QWidget.show = _show_off_desktop  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Never read or write the user's real QSettings or app data during tests."""
    QStandardPaths.setTestModeEnabled(True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    path = tmp_path_factory.mktemp("settings")
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(path))
    yield


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A modal dialog would block the test run: fail loudly instead."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"unexpected modal dialog: {args[1:3]!r}")

    for name in ("question", "warning", "critical", "information", "about"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(refuse))
    for name in ("getOpenFileName", "getSaveFileName"):
        monkeypatch.setattr(QFileDialog, name, staticmethod(refuse))
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(refuse))
