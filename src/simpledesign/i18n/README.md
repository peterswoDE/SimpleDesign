# Übersetzungen

Quellsprache der Oberfläche ist **Deutsch**: Alle Texte stehen deutsch in `self.tr("…")`
bzw. `QCoreApplication.translate("Kontext", "…")`.

Neue Sprache (z. B. Englisch, Phase 7):

```bash
uv run pyside6-lupdate src/simpledesign -ts src/simpledesign/i18n/simpledesign_en.ts
uv run pyside6-linguist src/simpledesign/i18n/simpledesign_en.ts
uv run pyside6-lrelease src/simpledesign/i18n/simpledesign_en.ts
```

Danach `"en"` in `SUPPORTED_LANGUAGES` in `ui/i18n.py` eintragen.
