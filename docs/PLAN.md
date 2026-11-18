# SimpleDesign – Projektplan

Ein einfaches, parametrisches CAD-Tool für Holzmöbel ("Fusion lite").
Python-Desktop-Anwendung, Oberfläche zunächst auf Deutsch, vorbereitet für Englisch.

---

## 1. Ziele

- **Einfach bedienbar**: Teil anklicken → Maße im Eigenschaften-Panel ändern → 3D-Ansicht aktualisiert sich sofort.
- **Abhängige Maße**: Ändert sich die Schrankhöhe, passen sich Seitenwände, Fachböden, Rückwand usw. automatisch an. Eigene Variablen und Formeln (`H - 2 * t`).
- **Drei Arten zu modellieren**:
  1. Bretter/Bauteile (Quader aus Rohware) frei platzieren
  2. Vorlagen (Regal, Schrank, Tisch …) mit Parametern
  3. Skizzen mit Constraints + Extrusion für komplexe Teile
- **Holzverbindungen**: Dübel/Schrauben, Nut/Falz/Fräsungen, Zapfen/Zinken – automatisch erzeugt und maßabhängig.
- **Ausgaben**: Zuschnittliste, Zuschnittoptimierung, technische Zeichnung (PDF/DXF), 3D-Export (STEP/STL/OBJ).

### Nicht-Ziele (vorerst)
- Keine Feature-Timeline wie in Fusion – Undo/Redo reicht.
- Kein Rendering/Fotorealismus, keine Kinematik, keine CNC-/CAM-Ausgabe.

---

## 2. Technologie-Stack

Geprüft am 18.11.2026 auf PyPI (alle Pakete aktiv gepflegt, letzte Releases Aug.–Nov. 2026, Windows-Pakete vorhanden). Grundsatz: **immer die neueste Version jedes Pakets**, keine veralteten Bibliotheken (siehe Abschnitt 2a).

| Bereich | Wahl (Stand) | Begründung |
|---|---|---|
| Sprache | **Python 3.14** | Neueste Version, die *alle* Abhängigkeiten unterstützen. Python 3.15 (Okt. 2026) wird von build123d/OCP und `slvs` noch nicht unterstützt → Umstieg, sobald sie es tun |
| Paketverwaltung | `uv` + `pyproject.toml` + `uv.lock` | Reproduzierbar; uv installiert Python selbst |
| GUI | **PySide6** (Qt 6) | Docks/Panels, `QUndoStack`, Übersetzungen (`tr()` / Qt Linguist) |
| 3D-Ansicht | **pyvista + pyvistaqt** (VTK) | Picking, Kamera, in Qt einbettbar |
| Geometrie-Kern | **build123d** (OpenCascade/OCP) | Volumenkörper, Boolesche Operationen, STEP/STL, Projektionen für Zeichnungen |
| Skizzen-Solver | **`slvs`** (offizielle SolveSpace-Python-Bindings) | Gepflegt, Pakete für 3.12–3.14. *Nicht* `python-solvespace` – das hat Windows-Pakete nur bis Python 3.11 |
| Formeln | eigener sicherer Ausdrucks-Parser (Python-`ast`, Whitelist) | Kein `eval`, gute Fehlermeldungen |
| Zuschnitt 2D | eigener Guillotine-Algorithmus (Referenz: `rectpack`) | `rectpack` ist seit Jahren ungepflegt; Guillotine-Schnitte + Faserrichtung + Sägeblattbreite brauchen ohnehin eigene Logik |
| Zuschnitt 1D | eigener First-Fit-Decreasing | Stangenware (Bauholz, Latten) |
| Zeichnung | OCC-HLR-Projektion → **ezdxf** (DXF) + PDF via Qt `QPdfWriter` | |
| Tests | pytest, pytest-qt, VTK offscreen | |
| Code-Qualität | ruff, mypy | |

## 2a. Aktualität & keine veralteten APIs

Ziel: Updates sollen nie zu großen Umbauten führen.

- **Keine veralteten Pakete**: Nur aktiv gepflegte Bibliotheken (Release im letzten Jahr, Support für aktuelles Python). Deshalb bereits ausgeschlossen: `python-solvespace` (nur bis Python 3.11) und `rectpack` (seit Jahren ohne Release).
- **Keine veralteten Funktionen**: In Tests gilt `filterwarnings = error::DeprecationWarning` und `error::PendingDeprecationWarning` (in `pyproject.toml`). Nutzt unser Code eine Funktion, die als veraltet markiert ist, **schlagen die Tests sofort fehl**. Ausnahmen nur für Warnungen *innerhalb* fremder Bibliotheken, einzeln begründet.
- Qt: nur aktuelle PySide6-APIs (z. B. `exec()` statt `exec_()`, `QScreen` statt `QDesktopWidget`, Enums voll qualifiziert wie `Qt.AlignmentFlag.AlignLeft`).
- **Untere Versionsgrenzen** in `pyproject.toml` = jeweils neueste Version beim Start (z. B. `PySide6>=6.12`), konkrete Versionen im `uv.lock`.
- **Regelmäßiges Update**: zu Beginn jeder Phase `uv lock --upgrade` + `uv python upgrade`, Tests laufen lassen, Warnungen sofort beheben. Kleine, häufige Updates statt seltener großer Sprünge.
- **Abhängigkeiten kapseln**: Fremdbibliotheken nur in wenigen Modulen benutzen (`core/geometry` für build123d, `core/sketch_solver` für slvs, `ui/viewport` für pyvista). Ändert eine Bibliothek ihre API, ist nur ein Modul betroffen.
- Ruff mit Regeln `UP` (pyupgrade) für moderne Python-Syntax.

### Lizenz-Hinweis
`slvs` (SolveSpace) steht unter **GPLv3**. Für den Eigengebrauch egal; **wenn das Tool später weitergegeben wird, muss es selbst GPLv3 sein.** Alternative, falls das nicht gewünscht ist: eigener Solver mit scipy (mehr Aufwand). → Entscheidung vor Phase 5, Solver wird hinter einer Schnittstelle (`SketchSolver`) gekapselt, damit er austauschbar bleibt.

---

## 3. Grundfestlegungen (verbindlich ab Phase 1)

Diese Punkte müssen vor dem ersten Bauteil feststehen – später ändern heißt alles umbauen.

### Koordinaten & Einheiten
- **Z zeigt nach oben**, X nach rechts, Y nach hinten (rechtshändig). Boden = XY-Ebene.
- Basiseinheit **mm**, Winkel in **Grad**. Intern `float`; Anzeige auf 0,1 mm gerundet.
- Jedes Brett hat ein **lokales Koordinatensystem**: Ursprung an einer Ecke, **Länge = lokale X-Achse = Faserrichtung**, Breite = lokal Y, Stärke = lokal Z. Position/Rotation verschieben dieses System.
  → Zuschnittliste und Faserrichtung ergeben sich direkt daraus, egal wie das Teil im Raum liegt.
- Rotationen zunächst nur in 90°-Schritten (deckt fast alle Möbel ab); freie Winkel später.

### Identität & Referenzen
- Jedes Objekt (Teil, Parameter, Material, Skizze, Verbindung) hat eine **unveränderliche interne ID** (UUID). Der Name ist nur Anzeige.
- In Formeln schreibt der Nutzer Namen (`Seite_links.x`); gespeichert wird intern mit ID. **Umbenennen bricht damit keine Formeln.**
- Löschen eines Teils, auf das sich Formeln beziehen: Warnung mit Liste der abhängigen Stellen; diese werden auf ihren letzten Wert eingefroren (nicht stillschweigend kaputt).
- Flächen-Referenzen (Einrasten, Skizzenebenen, Verbindungen) werden **semantisch** gespeichert („Oberseite von Teil X“, „Stirnseite Ende von Teil Y“), nie über OCC-Flächennummern.

### Formeln
- Erlaubt: Zahlen, `+ - * / ( )`, Parameter, `Teil.Eigenschaft`, Funktionen `min max round floor ceil abs`.
- **Dezimaltrenner**: In Formeln gilt der Punkt (`18.5`). Reine Zahleneingaben akzeptieren auch Komma (`18,5`) und werden umgewandelt. Funktionsargumente werden mit `;` getrennt (`max(a; b)`), damit es keine Verwechslung gibt.
- Namen dürfen Umlaute enthalten (`Höhe`), müssen aber eindeutig sein. Gültigkeitsbereich: global oder innerhalb einer Gruppe/Vorlage (`Korpus.H`).
- **Fehlerverhalten**: Ungültige Formel, Zyklus oder unsinniges Ergebnis (Maß ≤ 0) → Feld rot mit Erklärung, betroffene Teile behalten ihren **letzten gültigen Wert**. Das Modell ist nie in einem kaputten Zustand.

### Formel vs. Maus
- Ein Feld ist entweder **fester Wert** oder **Formel** (Symbol im Panel zeigt, was).
- Zieht man ein Teil mit Formel-Position per Maus, wird die Formel um einen Versatz ergänzt (`Seite_links.x + t` → `Seite_links.x + t + 25`), statt sie zu löschen. Rastet es an einem anderen Teil ein, wird eine neue Referenz-Formel erzeugt.
- Ein Mausziehen = ein Undo-Schritt (Zwischenpositionen werden zusammengefasst).

### Materialien
- Bibliothek im Benutzerordner (JSON), editierbar, eigene Rohware anlegbar.
- **Das Dokument speichert eine Kopie** der verwendeten Materialien. Datei bleibt öffnungsfähig, auch wenn das Material in der Bibliothek gelöscht/geändert wurde (Hinweis bei Abweichung, Aktualisieren auf Wunsch).

### Datei
- `.sdz` = JSON mit `format_version`; Migrationsfunktionen pro Versionssprung. Gespeichert werden Ausdrücke + IDs, nicht berechnete Werte.
- **Automatisches Sichern** alle 2 Minuten in eine Wiederherstellungsdatei; Angebot zur Wiederherstellung nach Absturz.

---

## 4. Architektur

Strikte Trennung: Der **Kern** kennt keine GUI und ist komplett testbar.

```
simpledesign/
├── core/                    # reine Logik, keine Qt-Abhängigkeit
│   ├── ids.py               # UUID-Erzeugung
│   ├── params/
│   │   ├── expression.py    # Parser/Evaluator, Name↔ID-Auflösung
│   │   └── graph.py         # Abhängigkeitsgraph, topologische Sortierung, Zyklen
│   ├── materials/           # Rohware-Modell + Bibliothek
│   ├── model/
│   │   ├── document.py
│   │   ├── part.py          # Brett, Extrusionsteil
│   │   ├── pattern.py       # Anordnungen (Anzahl als Formel)
│   │   ├── sketch.py        # Skizzen-Elemente + Constraints
│   │   ├── joint.py         # Verbindungen
│   │   └── refs.py          # Semantische Flächen-/Kanten-Referenzen
│   ├── geometry/            # build123d-Körper aus dem Modell, Cache
│   ├── sketch_solver/       # Schnittstelle + slvs-Implementierung
│   ├── templates/           # Vorlagen-Format + eingebaute Vorlagen
│   ├── cutlist/             # Zuschnittliste + Optimierung 1D/2D
│   ├── drawing/             # Ansichten, Bemaßung, DXF/PDF
│   └── io/                  # .sdz laden/speichern/migrieren, Export
├── app/
│   ├── commands.py          # Undo-fähige Befehle
│   └── controller.py        # Modell ↔ UI, Neuberechnung, Autosave
├── ui/
│   ├── main_window.py
│   ├── viewport.py          # 3D-Ansicht, Auswahl, Gizmo, Einrasten
│   ├── tree_panel.py
│   ├── properties_panel.py
│   ├── parameters_panel.py
│   ├── sketch_editor.py
│   └── dialogs/
└── i18n/                    # .ts/.qm (de, später en)
tests/
docs/
```

### Datenfluss bei einer Maßänderung
1. Nutzer tippt im Panel `H = 1800`.
2. `SetExpressionCommand` landet im Undo-Stack (speichert alten und neuen **Ausdruck**).
3. Abhängigkeitsgraph berechnet alle betroffenen Werte in topologischer Reihenfolge neu.
4. Nur betroffene Teile erzeugen ihre Geometrie neu (Cache-Schlüssel = berechnete Maße + Verbindungen).
5. Viewport, Zuschnittliste und Panels aktualisieren sich über Signale.

### Ein einziger Geometrie-Pfad
Alle Teile werden von Anfang an über build123d erzeugt – **kein separater „schneller Quader-Pfad“**, der später mit OCC-Teilen auseinanderlaufen würde. Ein Quader ohne Bearbeitung ist auch in OCC schnell; teuer sind nur Boolesche Operationen, und die werden pro Teil gecacht.
Ziel: < 100 ms Neuberechnung bei ~50 Teilen. Wird in Phase 0 gemessen; reicht es nicht, wird die Neuberechnung in einen Hintergrund-Thread verlegt (UI zeigt kurz alte Geometrie).

### Internationalisierung (vorbereitet)
- Alle UI-Texte über `self.tr("…")`; Kern liefert Meldungen als Schlüssel + Parameter, nicht als fertigen Text.
- `QTranslator` lädt `i18n/simpledesign_<lang>.qm`; Sprache in den Einstellungen.
- Zahlenanzeige über `QLocale`. Formel-Syntax bleibt sprachunabhängig (siehe Formeln).
- Eingebaute Material- und Vorlagennamen ebenfalls übersetzbar (Schlüssel statt Text).

---

## 5. Benutzeroberfläche

```
┌──────────────────────────────────────────────────────────────┐
│ Menü / Werkzeugleiste: Neu Öffnen Speichern | Brett Vorlage   │
│ Skizze Extrudieren Verbindung | Zuschnitt Zeichnung Export    │
├────────────┬─────────────────────────────────┬───────────────┤
│ Bauteil-   │                                 │ Eigenschaften │
│ baum       │          3D-Ansicht             │  Name         │
│            │   (Auswahl, Gizmo, Einrasten)   │  Material ▾   │
│ ▸ Korpus   │                                 │  Länge ƒ[H-2t]│
│   Seite L  │                                 │  Breite [ 400 ]│
│   Seite R  │                                 │  Stärke ƒ[ t ] │
│ ▸ Fächer×4 │                                 │  Position…    │
├────────────┴─────────────────────────────────┤               │
│ Parameter:  H=1800  B=800  T=400  t=18   [+]  │               │
└──────────────────────────────────────────────┴───────────────┘
```

- Maus wie Fusion: **Linksklick** Auswahl, **mittlere Taste ziehen** = Verschieben der Ansicht (Pan), **Shift + mittlere Taste** = Drehen (Orbit), **Rad** = Zoom zum Mauszeiger.
- Verschieben von Teilen per Gizmo (Pfeile entlang X/Y/Z) mit **Einrasten** an Flächen/Kanten anderer Teile; Raster optional.
- Standardansichten (vorne, oben, rechts, iso) per Tasten/Ansichtswürfel.
- Eingabefelder akzeptieren Zahl oder Formel; `ƒ` kennzeichnet Formeln, Fehler rot mit Tooltip.
- Teile auch ein-/ausblenden und transparent schalten (zum Arbeiten im Korpus-Inneren).

---

## 6. Phasen

Jede Phase endet mit einer nutzbaren Version, Tests und einem Beispielmöbel.

### Phase 0 – Projektgerüst & Technik-Prüfung
- `pyproject.toml`, uv mit Python 3.14, ruff, mypy, pytest – alles in der neuesten Version, Deprecation-Warnungen als Fehler.
- **Technik-Prüfung (Spike)** – bevor wir uns festlegen:
  - build123d, pyvistaqt, PySide6, `slvs` gemeinsam installieren.
  - 50 Quader per build123d erzeugen, tessellieren, in pyvista anzeigen → Zeit messen.
  - Ein Teil per Mausklick auswählen (Picking).
  - Einfache Skizze mit `slvs` lösen.
- Leeres Hauptfenster mit Viewport, Docks, Menü, i18n-Infrastruktur.
- **Fertig, wenn**: App startet, zeigt Gitter + Achsen, Spike-Messwerte sind dokumentiert, Tests laufen.

### Phase 1 – MVP: Bretter + Parameter + Zuschnittliste
- Grundfestlegungen aus Abschnitt 3 umsetzen (IDs, Koordinaten, Formeln, Fehlerverhalten).
- Ausdrucks-Parser + Abhängigkeitsgraph.
- Parameter-Leiste (global) und Gruppen mit eigenen Parametern.
- Brett hinzufügen, auswählen, Maße/Position per Formel, Gizmo, Einrasten.
- **Anordnung (Muster)**: Teil n-mal mit Abstand wiederholen, **Anzahl als Formel** (z. B. 4 Fachböden gleichmäßig verteilt).
- Bauteilbaum: Gruppen, Umbenennen, Duplizieren, Löschen, Ein-/Ausblenden.
- Undo/Redo.
- Kleine eingebaute Materialliste (Multiplex 18 mm, Leimholz 18 mm, KVH 60×80 …).
- Zuschnittliste: Tabelle + CSV. Teile werden zusammengefasst, wenn **Material, Maße und Faserrichtung** gleich sind (später auch: gleiche Bearbeitungen).
- Speichern/Laden, Autosave.
- **Fertig, wenn**: Korpus mit Fachböden bauen, Höhe ändern → alles passt sich an, Fachboden-Anzahl ändern → Abstände passen, Zuschnittliste stimmt, Datei lässt sich wieder öffnen.

### Phase 2 – Materialbibliothek, Zuschnittoptimierung, 3D-Export
- Material-Bibliothek mit Editor: Plattenware, Massivholz/Bohlen, Bauholz, **eigene Rohware**.
- Faserrichtung pro Teil beachten/ignorieren (bei MDF egal).
- 2D-Optimierung: Guillotine-Schnitte, Sägeblattbreite, Besäumrand, grafischer Schnittplan.
- 1D-Optimierung für Stangenware (mit Kappzugabe).
- Materialbedarf + optional Kosten.
- Export: STEP, STL, OBJ.

### Phase 3 – Holzverbindungen
*(vor die Vorlagen gezogen, damit Vorlagen gleich mit Verbindungen gebaut werden – z. B. Rückwand in Nut)*
- Grundprinzip: Eine Verbindung hat ein **Hauptteil** und ein **Anschlussteil**. Sie darf
  - Material entfernen (Bohrung, Nut, Zapfenloch) und
  - **Maße des Anschlussteils verlängern** (Zapfenlänge, Einstand in Nut) – diese Zugabe fließt automatisch in die Zuschnittliste ein (Rohmaß ≠ sichtbares Maß).
- 3a: Dübel und Schrauben (Bohrbild nach einstellbaren Regeln), Zusatzteile in der Stückliste (Beschläge).
- 3b: Nut, Falz, Taschen/Fräsungen.
- 3c: Zapfen, Fingerzinken, Schwalbenschwanz.
- Zuschnittliste fasst Teile nur noch zusammen, wenn auch die Bearbeitungen gleich sind.

### Phase 4 – Vorlagen
- **Vorlage = gespeicherte Gruppe mit freigegebenen Parametern** (gleiches Format wie ein Dokumentausschnitt). Dadurch gibt es nur ein Format: eingebaute Vorlagen und eigene „Gruppe als Vorlage speichern“ funktionieren identisch.
- Eingebaut: Regal, Korpus/Schrank (Fachböden, Rückwand in Nut), Tisch, Bank.
- Nach dem Einfügen frei weiterbearbeitbar; Vorlagen-Parameter steuern weiter.

### Phase 5 – Skizzen + Extrusion
- Vorher: Entscheidung GPL/`slvs` vs. eigener Solver (siehe Lizenz-Hinweis).
- Skizzenmodus auf Hauptebene oder Teilefläche (semantische Referenz).
- Elemente: Linie, Rechteck, Kreis, Bogen; später Spline.
- Constraints: horizontal, vertikal, parallel, rechtwinklig, tangential, koinzident, gleich, Bemaßung (mit Formeln).
- Solver-Status anzeigen (unter-/vollständig/überbestimmt), bei Nicht-Lösbarkeit letzten gültigen Zustand behalten.
- Extrudieren als neues Teil (mit Material + Faserrichtung) oder als Ausschnitt aus bestehendem Teil.
- Extrusionsteile in der Zuschnittliste mit **Hüllmaß** in ihrem lokalen System (Rohmaß zum Aussägen).

### Phase 6 – Technische Zeichnung
- Ansichten (vorne, seitlich, oben, iso) per HLR-Projektion.
- Automatische Gesamt- und Teilbemaßung, manuelle Bemaßung ergänzbar.
- Einzelteilzeichnungen mit Bohrungen/Fräsungen.
- A4/A3, Schriftfeld; Export PDF + DXF.

### Phase 7 – Englisch & Feinschliff
- Englische Übersetzung, Sprachumschaltung.
- Einstellungen, Tastenkürzel, freie Rotationswinkel, Performance.
- Installer (PyInstaller/Briefcase) – OCC/VTK sind groß, Paketgröße früh testen.

---

## 7. Qualität & Arbeitsweise

- Kern (`core/`) mit hoher Testabdeckung; **Beispielmöbel als Regressionstests** (Zuschnittliste und Maße müssen stabil bleiben).
- Tests für Formel-Randfälle: Zyklen, Division durch 0, gelöschte Referenzen, Umbenennen, Komma-Eingaben.
- Speicherformat: Jede Version legt eine Beispieldatei in `tests/files/` ab; alte Dateien müssen immer ladbar bleiben.
- Kleine Schritte, je Feature ein Branch/Commit; Code verständlich und kommentiert, da wir gemeinsam entwickeln.
- Code und Kommentare auf Englisch, Oberfläche auf Deutsch.

---

## 8. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Abhängigkeiten passen nicht zusammen (Python-Version, Windows-Pakete) | Phase-0-Spike, `uv.lock`; geprüft: Python 3.14 ist die neueste gemeinsame Basis |
| Bibliothek ändert API / wird eingestellt | Abschnitt 2a: Deprecations als Testfehler, regelmäßige kleine Updates, Kapselung in einzelnen Modulen |
| OpenCascade zu langsam für „live“ | Cache pro Teil, nur betroffene Teile neu, notfalls Hintergrund-Thread; früh messen |
| Formeln brechen durch Umbenennen/Löschen | Interne IDs, Warnung + Einfrieren beim Löschen |
| Topologische Referenzen (Flächen ändern sich) | Semantische Referenzen statt OCC-Flächen-IDs |
| Zuschnittliste falsch durch Verbindungen | Verbindungen geben Zugaben explizit an; Regressionstests mit Beispielmöbeln |
| GPL-Pflicht durch `slvs` | Solver hinter Schnittstelle, Entscheidung vor Phase 5 |
| Umfang wächst schnell | Strikte Phasen, jede für sich nutzbar |

---

## 9. Offene Punkte (später klären)

- Genaue Regeln für Dübel-/Schraubenabstände (eigene Vorgaben?) – vor Phase 3.
- Welche Schnittmaschine (Formatkreissäge, Tauchsäge, Baumarkt-Zuschnitt) → Optimierungsregeln – vor Phase 2.
- Kantenumleimer/Furnierkanten in der Zuschnittliste (Maßabzug?) – vor Phase 2.
- Rundungen/Fasen an Kanten – frühestens Phase 5.
- Soll das Tool je weitergegeben werden (Lizenzfrage)? – vor Phase 5.
