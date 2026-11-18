from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Never read or write the user's real QSettings during tests."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    path = tmp_path_factory.mktemp("settings")
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(path))
    yield
