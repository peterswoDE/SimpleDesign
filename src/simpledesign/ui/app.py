"""Application bootstrap."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from simpledesign import __version__
from simpledesign.ui.i18n import SOURCE_LANGUAGE, install_translators
from simpledesign.ui.main_window import MainWindow

ORGANIZATION = "SimpleDesign"
APPLICATION = "SimpleDesign"


def configure_application(app: QApplication) -> None:
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName(APPLICATION)
    app.setApplicationVersion(__version__)
    language = str(QSettings().value("ui/language", SOURCE_LANGUAGE))
    # Keep a reference on the app object so the translators are not garbage collected.
    app.setProperty("_translators", install_translators(app, language))


def run(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv) if argv is not None else sys.argv)
    configure_application(app)
    window = MainWindow()
    window.show()
    return app.exec()
