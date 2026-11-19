# SimpleDesign – Projektplan

Ein einfaches, parametrisches CAD-Tool für Holzmöbel ("Fusion lite").
Python-Desktop-Anwendung, Oberfläche auf Deutsch (Texte zentral gesammelt, Übersetzung später möglich).

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
| Sprache | **Python 3.14** | Neueste Version, die *alle* Abhängigkeiten unterstützen. Python 3.15 (Okt. 2026) wird von build123d/OCP noch nicht unterstützt → Umstieg, sobald sie es tun |
| Paketverwaltung | `uv` + `pyproject.toml` + `uv.lock` | Reproduzierbar; uv installiert Python selbst |
| GUI | **PySide6** (Qt 6) | Docks/Panels, `QUndoStack`, Übersetzungen (`tr()` / Qt Linguist) |
| 3D-Ansicht | **pyvista + pyvistaqt** (VTK) | Picking, Kamera, in Qt einbettbar |
| Geometrie-Kern | **build123d** (OpenCascade/OCP) | Volumenkörper, Boolesche Operationen, STEP/STL, Projektionen für Zeichnungen |
| Skizzen-Solver | **eigener Solver** mit numpy (gedämpftes Gauß-Newton, Freiheitsgrade per SVD-Rang) | Keine GPL-Abhängigkeit (`slvs`/SolveSpace ist GPLv3, ausgeschlossen wegen möglicher späterer Lizenzvergabe). numpy ist ohnehin Abhängigkeit (BSD) |
| Formeln | eigener sicherer Ausdrucks-Parser (eigener Tokenizer + rekursiver Abstieg) | Kein `eval`, gute Fehlermeldungen mit Position |
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
- **Abhängigkeiten kapseln**: Fremdbibliotheken nur in wenigen Modulen benutzen (`core/geometry` für build123d, `core/sketch` für den Solver, `ui/viewport` für pyvista). Ändert eine Bibliothek ihre API, ist nur ein Modul betroffen.
- Ruff mit Regeln `UP` (pyupgrade) für moderne Python-Syntax.

### Lizenz-Hinweis
**Entschieden (18.11.2026):** Das Tool soll eventuell später lizenziert/verkauft werden → **keine GPL-/Copyleft-Abhängigkeiten.** `slvs` (SolveSpace, GPLv3) entfällt; der Skizzen-Solver ist eine Eigenentwicklung hinter der Schnittstelle `SketchSolver`. Erlaubt: MIT/BSD/Apache, LGPL nur dynamisch gelinkt (PySide6/Qt). Jede neue Abhängigkeit wird vorher auf ihre Lizenz geprüft.

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
│   ├── sketch_solver/       # Schnittstelle + eigener numpy-Solver
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

> **Stand Phase 1:** Quader werden direkt mit OCP (`BRepPrimAPI_MakeBox`) erzeugt und tesselliert – das ist derselbe OpenCascade-Kern, aber ohne die ~6 s Importzeit von build123d (OCP allein ~0,6 s, im Hintergrund vorgeladen). build123d kommt dazu, sobald 3D-Export (Phase 2) bzw. Holzverbindungen mit Booleschen Operationen (Phase 3) es brauchen.
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
  - build123d, pyvistaqt, PySide6 gemeinsam installieren.
  - 50 Quader per build123d erzeugen, tessellieren, in pyvista anzeigen → Zeit messen.
  - Ein Teil per Mausklick auswählen (Picking).
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

> **Stand Phase 2 (umgesetzt):** Materialbibliothek als `materials.json` im Benutzerverzeichnis; das Dokument behält eine eigene Kopie jedes Materials und fragt nach Bibliotheksänderungen, ob es aktualisieren soll. Schnittplan-Dock mit Bedarf, Kosten und PDF-Export. STEP wird direkt über OCP (XCAF, mit Teilenamen) geschrieben, build123d ist dafür nicht nötig.

### Phase 3 – Holzverbindungen
*(vor die Vorlagen gezogen, damit Vorlagen gleich mit Verbindungen gebaut werden – z. B. Rückwand in Nut)*
- Grundprinzip: Eine Verbindung hat ein **Hauptteil** und ein **Anschlussteil**. Sie darf
  - Material entfernen (Bohrung, Nut, Zapfenloch) und
  - **Maße des Anschlussteils verlängern** (Zapfenlänge, Einstand in Nut) – diese Zugabe fließt automatisch in die Zuschnittliste ein (Rohmaß ≠ sichtbares Maß).
- 3a: Dübel und Schrauben (Bohrbild nach einstellbaren Regeln), Zusatzteile in der Stückliste (Beschläge).
- 3b: Nut, Falz, Taschen/Fräsungen.
- 3c: Zapfen, Fingerzinken, Schwalbenschwanz.
- Zuschnittliste fasst Teile nur noch zusammen, wenn auch die Bearbeitungen gleich sind.

> **Stand Phase 3a+3b (umgesetzt):** Dübel, Schrauben, Nut und Falz. Eine Verbindung entsteht aus zwei gewählten Brettern (zuerst gewähltes = Hauptteil) über das Menü „Verbindung“ und wird im Dock „Verbindungen“ bearbeitet. Bohrbild: Randabstand 50 mm, max. Abstand 150 mm (Dübel) bzw. 200 mm (Schrauben), Anzahl automatisch (mind. 2) oder fest. Nut/Falz verlängern das Anschlussteil um die Tiefe minus Spiel (Rohmaß in Zuschnittliste und Schnittplan). Bohrungen/Ausschnitte werden mit OCP-Booleschen Operationen erzeugt (Anzeige und STEP/STL/OBJ). Zuschnittliste mit Spalte „Bearbeitung“ und Beschläge-Tabelle. Dateiformat Version 3. Offen: Taschen/Fräsungen (3b).

> **Stand Phase 3c (umgesetzt):** Zapfen (blind, Dicke ⅓, Absatz seitlich, Zapfenloch 1 mm tiefer), Fingerzinken (Anzahl automatisch ungerade, etwa Materialstärke breit) und durchgehender Schwalbenschwanz (Schwänze am Anschlussteil, Neigung 1:6, Zinkenbreite außen 8 mm). Eckverbindungen verlängern das Anschlussteil um die Dicke des Hauptteils. Schrägflächen als Prisma-Bearbeitung. Zuschnittliste zählt „Fräsungen“ pro Verbindung.

### Phase 4 – Vorlagen
- **Vorlage = gespeicherte Gruppe mit freigegebenen Parametern** (gleiches Format wie ein Dokumentausschnitt). Dadurch gibt es nur ein Format: eingebaute Vorlagen und eigene „Gruppe als Vorlage speichern“ funktionieren identisch.
- Eingebaut: Regal, Korpus/Schrank (Fachböden, Rückwand in Nut), Tisch, Bank.
- Nach dem Einfügen frei weiterbearbeitbar; Vorlagen-Parameter steuern weiter.

> **Stand Phase 4 (umgesetzt):** Vorlagen sind `.sdt`-Dateien (sdz-JSON mit einer obersten Gruppe; deren Parameter sind die Vorlagen-Parameter) im Ordner `templates` neben den Wiederherstellungsdateien. „Einfügen → Vorlage…“ zeigt eingebaute und eigene Vorlagen, Parameter lassen sich vor dem Einfügen ändern; die Vorlage landet rechts neben dem Vorhandenen (Abstand 200 mm), Namen werden eindeutig gemacht (`Seite_links_2`, `H_2`). „Gruppe als Vorlage speichern…“ (Kontextmenü/Einfügen) ersetzt Bezüge nach außen durch ihre aktuellen Werte. Eingebaut: Regal, Korpus/Schrank (Rückwand in Nut), Tisch (Zapfen), Bank.

### Phase 5 – Skizzen + Extrusion
- Eigener Solver (siehe Lizenz-Hinweis), austauschbar hinter `SketchSolver`.
- Skizzenmodus auf Hauptebene oder Teilefläche (semantische Referenz).
- Elemente: Linie, Rechteck, Kreis, Bogen; später Spline.
- Constraints: horizontal, vertikal, parallel, rechtwinklig, tangential, koinzident, gleich, Bemaßung (mit Formeln).
- Solver-Status anzeigen (unter-/vollständig/überbestimmt), bei Nicht-Lösbarkeit letzten gültigen Zustand behalten.
- Extrudieren als neues Teil (mit Material + Faserrichtung) oder als Ausschnitt aus bestehendem Teil (deckt auch Taschen/Fräsungen ab, die in Phase 3 offen blieben).
- Extrusionsteile in der Zuschnittliste mit **Hüllmaß** in ihrem lokalen System (Rohmaß zum Aussägen).

> **Stand Phase 5 (umgesetzt):** Eigener numpy-Solver (Newton/Levenberg-Marquardt, Freiheitsgrade über den Rang der Jacobi-Matrix, widersprüchliche Bedingungen werden markiert). Skizzen liegen am Brett: als **Kontur** (das Brett wird zum Extrusionsteil; Länge/Breite = Hüllmaß der Kontur, in der Zuschnittliste das Rohmaß) oder als **Ausschnitt** auf Ober-/Unterseite (Tasche mit Tiefe als Formel oder durchgehend, Bezugskanten des Bretts sind fangbar). Maße sind Formeln (`d.<id>` bzw. `depth.<id>` im Brett), Parameter steuern weiter. Skizzeneditor: Linie (Kette), Rechteck, Kreis, Bogen, Hilfsgeometrie, Ziehen, Rahmenauswahl, Bemaßen (D), H/V-Maß, Winkel, horizontal/vertikal/parallel/senkrecht/tangential/deckungsgleich/gleich/fixieren, eigenes Rückgängig; OK nur bei geschlossener, lösbarer Kontur. Einstieg: „Einfügen → Skizzenteil…“ und Abschnitt „Skizze“ in den Eigenschaften. Dateiformat Version 4. Splines folgen später.

### Phase 6 – Technische Zeichnung
- Ansichten (vorne, seitlich, oben, iso) per HLR-Projektion.
- Automatische Gesamt- und Teilbemaßung, manuelle Bemaßung ergänzbar.
- Einzelteilzeichnungen mit Bohrungen/Fräsungen.
- A4/A3, Schriftfeld; Export PDF + DXF.

> **Stand Phase 6 (umgesetzt):** „Datei → Zeichnung…“ (Strg+P) zeigt die Blätter mit Vorschau und Blättern. Übersichtsblatt: Vorder-, Seiten- (rechts daneben) und Draufsicht (darunter, Projektionsmethode 1) plus Iso-Ansicht, HLR mit verdeckten Kanten (abschaltbar), Gesamtmaße, Normmaßstab automatisch. Pro Zuschnitt-Position ein Einzelteilblatt: Fläche + zwei Kantenansichten mit L/B/D, Bohrungen nummeriert mit Bohrtabelle (X/Y/Z, Ø, Tiefe). Schriftfeld mit Titel, Material, Anzahl, Maß, Maßstab, Datum, Blatt n von m. Export als PDF (maßstäblich, eine Seite je Blatt) und DXF (ezdxf, MIT; Layer SICHTBAR/VERDECKT/MASS/TEXT/RAHMEN, mm). Manuelle Bemaßung → Phase 8.

### Phase 7 – Einstellungen & Auslieferung
- Englische Übersetzung entfällt vorerst (Texte bleiben zentral in `ui/texts.py`).
- Einstellungen, Tastenkürzel, freie Rotationswinkel, Performance.
- Installer (PyInstaller/Briefcase) – OCC/VTK sind groß, Paketgröße früh testen.

> **Stand Phase 7 (umgesetzt):** Freie Drehung je Brett (Formel „Winkel“ in Grad, Achse X/Y/Z durch die Positionsecke); wirkt in 3D-Ansicht, Export (STEP/STL/OBJ) und Übersichtszeichnung, Zuschnittliste/Einzelteile bleiben in Teilkoordinaten. Gedrehte Teile werden nicht automatisch verbunden (Fehlermeldung an der Verbindung). Extras → Einstellungen (Strg+,): Einrasten + Fangabstand, Sicherungsintervall, Standardmaße neuer Bretter (QSettings). Extras → Tastenkürzel: alle Befehle, frei belegbar, Doppelbelegung wird geblockt, Standard wiederherstellbar. Performance: Formel-Parser gecacht, 3D-Aktoren werden wiederverwendet (156 Teile: Neuaufbau 126 → 19 ms). Auslieferung: `uv run --group build python tools/build_app.py` → `dist/SimpleDesign` (PyInstaller, ein Ordner, ca. 780 MB, davon VTK 290 MB, OCC 170 MB, Qt 100 MB). Englisch entfällt vorerst.

### Phase 8 – Feinschliff
Nur Politur, keine neuen Großfunktionen.
- **Bedienung reparieren**: Maus-/Kamerasteuerung (Drehen, Verschieben, Zoom auf Cursor), Auswahl, Gizmo und Einrasten zuverlässig machen; jede Interaktion mit Test bzw. klarem Verhalten.
- **Drag & Drop**: Vorlagen/Möbel aus einer Bibliothek in die 3D-Ansicht ziehen, Teile im Baum per Drag & Drop umgruppieren, Teile in der Ansicht ziehen.
- **Vordefinierte Möbel**: Vorlagenbibliothek mit Vorschaubildern (Regal, Schrank, Kommode, Tisch, Bank, Nachttisch, Schreibtisch …).
- Fehlende Werkzeuge ergänzen (Spiegeln, Ausrichten, Anordnen, Messen), Kontextmenüs, Statuszeilen-Hinweise.
- Konsistente Dialoge, Fehlermeldungen, Symbole.

> **Stand Phase 8 (umgesetzt):** Steuerung neu: Drehen mit mittlerer Maustaste (Drehteller um den Punkt unter dem Cursor, Z bleibt oben), Verschieben mit Umschalt+Mitte, Zoom auf den Cursor; Linksklick wählt, Ziehen eines Teils verschiebt es auf der Bodenebene (Esc bricht ab, ein Rückgängig-Schritt), Gizmo-Pfeile werden im Bildschirmraum gepickt und bewegen auch Gruppen. Rechtsklick öffnet ein Kontextmenü (Teilaktionen, Anordnen, Brett/Vorlage einfügen, Ansichten), Bedienhinweis in der Statuszeile. Möbelbibliothek als Dock mit Vorschaubildern; 11 eingebaute Möbel (u. a. Kommode, Nachttisch, Hängeschrank, Sideboard, Schreibtisch, Couchtisch, Bett) plus eigene Vorlagen; per Doppelklick oder Drag & Drop in die 3D-Ansicht (landet auf dem Boden unter dem Cursor, 10-mm-Raster). Teile im Baum per Drag & Drop umgruppieren. Menü „Anordnen“: am zuerst gewählten Teil ausrichten (je Achse bündig/mittig) und gleichmäßig verteilen. Messen: Statuszeile zeigt Größe der Auswahl und bei zwei Teilen den lichten Abstand je Achse. Dialoge sind nicht-modal. Spiegeln entfällt bewusst (kollidiert mit Positionsformeln; symmetrische Teile über Formeln/Muster).

> **Phase 8, Erweiterungen (umgesetzt):** Ziehgriffe zum Ändern der Größe, Drehringe zum freien Drehen, genaue Werte während des Ziehens eintippen. Einfüge-Werkzeuge: Trennwand (D), Fachboden (F) und Rückwand passen sich ins Fach ein und werden per Formel verknüpft; „In Nut setzen“ mit Nuttiefe, Spiel und Abstand (Standard in den Einstellungen); **Messen (M)** misst den Abstand zweier paralleler Flächen (Fläche wird hervorgehoben); Punkte messen (Umschalt+M) mit Fang an Ecken. **Abstand setzen (E):** Bezugsfläche klicken, dann die parallele Fläche des zu verschiebenden Teils, neuen Abstand tippen, Enter – das Teil wird einmalig verschoben (keine Verknüpfung). Reihe kopieren, Auswahl isolieren (I), Röntgenansicht (Alt+X). **Rückwände** (Teil-Eigenschaften): „Ist Rückwand“ und „Abstand zur Hinterkante“ je Rückwand; Teile, die in die Rückwand hineinragen oder an ihr enden, werden beim Festlegen/Ändern der Rückwand automatisch bis zu ihrer Vorderseite gekürzt und per Formel verknüpft (folgen also späteren Änderungen). „Geht durch die Rückwand“ führt ein Teil bis zur Hinterkante und teilt die Rückwand in zwei Teile, beide in Nuten; Abwählen fügt sie wieder zusammen. **Auftrennen (A):** Teile, durch die die Auswahl ganz hindurchgeht (z. B. Fachböden an einer durchgehenden Trennwand), werden an ihr in Stücke geteilt; die Stückenden sind Formeln auf die Trennflächen, die Stücke werden mit dem trennenden Teil verbunden (Fachboden wählen, um stattdessen die Trennwand zu teilen). Werkzeugleiste in kleinen beschrifteten Gruppen (Datei, Bearbeiten, Einfügen, Einpassen, Werkzeuge, Ansicht); jede Schaltfläche zeigt ihr Tastenkürzel in Klammern und folgt Änderungen im Tastenkürzel-Dialog.

### Phase 9 – Werkzeuge für die Fertigung
Ergebnis eines Vergleichs mit Fusion, SketchUp/OpenCutList, Polyboard, Cabinet Vision und Mozaik. Reihenfolge = Umsetzungsreihenfolge, jedes Werkzeug ein eigener Commit.

**Hoher Nutzen**
1. ✅ **Kollisionsprüfung** – Teile, die sich überschneiden, werden rot hervorgehoben und aufgelistet (Menü + dauerhafte Anzeige abschaltbar). Gewollte Überlappungen durch Verbindungen (Nut, Falz, Zapfen …) zählen nicht: geprüft wird die bearbeitete Geometrie. *Umgesetzt:* Ansicht → Kollisionen (K), Gruppe „Prüfen“; Überschneidungsvolumen rot über den Teilen, Paare in der Statuszeile. Dabei behoben: abgesetzte Nuten der Seiten reichen jetzt über die ganze Rückwand (Ecken überschnitten sich).
2. ✅ **Kantenumleimer** – je Brettkante (vorne/hinten/links/rechts) Umleimer mit Material und Dicke; Zuschnittliste: Rohmaß = Fertigmaß minus Umleimer, Laufmeter je Umleimer-Material, Kantensymbole in Zeichnung und Etikett. *Umgesetzt:* Eigenschaften → „Kanten (Umleimer)“ mit „Alle Kanten“ und L1/L2/W1/W2 (beschriftet mit der Möbelseite), 7 eingebaute Umleimer (ABS, Furnier, Anleimer) plus eigene; Zuschnittliste mit Spalte „Kanten“ und Laufmeter-Tabelle (+30 mm Überstand je Kante), CSV; Teilzeichnung mit Feld „Kanten“ und dicken Strichen an den Kanten. Etikett folgt mit Punkt 5.
3. **Türen- und Schubladen-Generator** – Einpassen-Werkzeuge wie Trennwand/Fachboden: Fach anklicken → Tür (aufliegend/einliegend, Fuge, ein-/zweiflügelig) bzw. Schublade (Front, Zarge, Boden, Auszugsluft), per Formel an das Fach gebunden.
4. **Beschläge mit Bohrbild** – Topfband 35 mm mit Montageplatte, Lochreihe System 32 für Bodenträger, Schubladenauszüge; Bohrungen erscheinen in 3D, Zeichnung, Bohrtabelle und Beschläge-Liste.
5. **Etiketten** – je Teil ein Aufkleber (Name, Fertig-/Rohmaß, Material, Faserrichtung, Umleimerkanten, Möbel, QR-Code) als PDF für gängige Etikettenbögen.

**Mittlerer Nutzen**
6. **Explosionsansicht & Montageanleitung** – Teile auseinandergezogen anzeigen (Regler), Montageschritte nach Verbindungen als PDF.
7. **Schnittansicht** – Schnittebene (X/Y/Z, verschiebbar) schneidet die Ansicht auf; Schnittflächen schraffiert.
8. **Bis Fläche ziehen** – Fläche eines Teils anklicken, dann Zielfläche: das Maß wird als Formel an die Zielfläche gebunden (Push/Pull mit Verknüpfung).
9. **Varianten** – mehrere benannte Parametersätze je Dokument, umschaltbar; Zuschnittliste/Export je Variante.
10. **Maserung über Fronten** – Teile zu einer Maserungsgruppe zusammenfassen; der Schnittplan legt sie zusammenhängend aus einem Stück.
11. **CNC-Export** – je Teil eine DXF mit Layern je Bearbeitung (Kontur, Bohrung je Ø/Tiefe, Tasche/Nut je Tiefe) für Nesting-Fräsen.

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
| Eigener Skizzen-Solver konvergiert schlecht | Gedämpftes Verfahren, Start vom letzten gültigen Zustand, Tests mit typischen Skizzen; Solver austauschbar |
| Umfang wächst schnell | Strikte Phasen, jede für sich nutzbar |

---

## 9. Offene Punkte (später klären)

- ~~Genaue Regeln für Dübel-/Schraubenabstände~~ – geklärt: Randabstand 50, max. Abstand 150/200 mm, pro Verbindung einstellbar.
- Welche Schnittmaschine (Formatkreissäge, Tauchsäge, Baumarkt-Zuschnitt) → Optimierungsregeln – vor Phase 2.
- ~~Kantenumleimer/Furnierkanten in der Zuschnittliste~~ – Phase 9 (Maßabzug: ja, Rohmaß = Fertigmaß minus Umleimer).
- Rundungen/Fasen an Kanten – frühestens Phase 5.
- ~~Soll das Tool je weitergegeben werden (Lizenzfrage)?~~ – geklärt: evtl. Verkauf → keine GPL-Abhängigkeiten.
