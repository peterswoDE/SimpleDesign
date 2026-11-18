"""Application bootstrap."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from simpledesign import __version__
from simpledesign.core.geometry import mesh
from simpledesign.core.io import sdz
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
    mesh.preload()  # import the geometry kernel while the window opens
    args = list(argv) if argv is not None else sys.argv
    app = QApplication(args)
    configure_application(app)
    window = MainWindow()
    window.show()
    window.show_library_error()
    window.offer_recovery()
    files = [Path(a) for a in args[1:] if a.lower().endswith(sdz.FILE_SUFFIX)]
    if files:
        window.open_path(files[0])
    return app.exec()
