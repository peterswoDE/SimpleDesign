from __future__ import annotations

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from simpledesign.ui.i18n import I18N_DIR, SOURCE_LANGUAGE, install_translators


def test_source_language_is_german() -> None:
    assert SOURCE_LANGUAGE == "de"
    assert I18N_DIR.is_dir()


def test_missing_language_falls_back_without_error(qtbot: QtBot) -> None:
    app = QApplication.instance()
    assert app is not None
    translators = install_translators(app, "xx")
    for translator in translators:
        app.removeTranslator(translator)
    install_translators(app, "de")


def test_german_locale_uses_decimal_comma(qtbot: QtBot) -> None:
    app = QApplication.instance()
    assert app is not None
    install_translators(app, "de")
    assert QLocale().toString(18.5, "f", 1) == "18,5"
