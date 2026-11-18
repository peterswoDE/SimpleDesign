"""Translation setup.

German is the source language: all UI strings are written in German inside
``tr()`` calls. Other languages are loaded from ``simpledesign/i18n/*.qm``.
See ``src/simpledesign/i18n/README.md`` for the workflow.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

SOURCE_LANGUAGE = "de"
# Add "en" here once simpledesign_en.qm exists (phase 7).
SUPPORTED_LANGUAGES: tuple[str, ...] = ("de",)

I18N_DIR = Path(__file__).resolve().parent.parent / "i18n"


def install_translators(app: QCoreApplication, language: str) -> list[QTranslator]:
    """Install Qt's own and SimpleDesign's translators for ``language``.

    Returns the installed translators (they must stay alive as long as the app).
    Missing translation files are not an error - the UI then falls back to German.
    """
    installed: list[QTranslator] = []

    # Qt's standard dialogs (file dialog, message box buttons …)
    qt_translator = QTranslator(app)
    qt_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load(QLocale(language), "qtbase", "_", qt_dir):
        app.installTranslator(qt_translator)
        installed.append(qt_translator)

    if language != SOURCE_LANGUAGE:
        own = QTranslator(app)
        if own.load(f"simpledesign_{language}", str(I18N_DIR)):
            app.installTranslator(own)
            installed.append(own)

    # Number formatting (decimal comma etc.) follows the UI language.
    QLocale.setDefault(QLocale(language))
    return installed
