from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QFileDialog, QMessageBox


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
