# Phase 0 – Ergebnisse der Technik-Prüfung

Stand: 18.11.2026 · Windows 11 · Python 3.14.10 · Skript: [`tools/spike.py`](../tools/spike.py)

```
uv run python tools/spike.py
```

## Versionen (alle neueste auf PyPI)

| Paket | Version |
|---|---|
| PySide6 | 6.12.0 |
| pyvista / pyvistaqt / vtk | 0.49.2 / 0.13.2 / 9.7.2 |
| build123d / cadquery-ocp | 0.13.0 / 8.0.1.1.0 |
| slvs (SolveSpace) | 3.3 |
| pytest / pytest-qt / ruff / mypy | 9.2.0 / 4.5.0 / 0.17.5 / 2.4.1 |

Keine einzige `DeprecationWarning` beim Durchlauf.

## Messwerte

Jeweils bester von 3 Läufen, immer mit frisch erzeugten Körpern (OCC merkt sich Triangulierungen pro Körper, Wiederholung am selben Körper wäre geschönt).

| Messung | Zeit |
|---|---|
| 50 Bretter erzeugen (ohne Bohrungen) | ~20–40 ms |
| 50 Bretter erzeugen + tessellieren, `build123d.tessellate()` | ~236 ms |
| 50 Bretter erzeugen + tessellieren, **OCP direkt** | **~91 ms** |
| 50 Bretter mit je 4 Bohrungen (Boolean) | ~560 ms |
| 50 gebohrte Bretter erzeugen + tessellieren (OCP) | ~1 090 ms |
| 1 Brett ändern, ohne Bohrungen | ~2 ms |
| 1 Brett ändern, mit 4 Bohrungen | ~21 ms |
| 10 Bretter ändern (typische Höhenänderung) | ~34 ms |
| Neu rendern (50 Teile) | ~2 ms |
| Skizze lösen (Rechteck, 6 Constraints) | < 1 ms |
| Import build123d | **5–8 s** |
| Import pyvista + vtk | 1–2 s |

## Ergebnisse & Konsequenzen

1. **Live-Neuberechnung ist machbar** – aber nur mit Cache pro Teil. Eine typische Änderung (≈ 10 Bretter) liegt bei ~35 ms. Alles komplett neu zu rechnen wäre mit Verbindungen > 1 s → Cache + „nur betroffene Teile“ ist Pflicht (wie geplant).
2. **Tessellieren direkt über OCP**, nicht über `build123d.tessellate()` (2,5–5× schneller). Kommt in `core/geometry/mesh.py`.
3. **build123d-Import dauert 5–8 s** (zieht scikit-learn und scipy mit). Konsequenz: Das Fenster startet ohne Geometrie-Kern; der Kern wird im Hintergrund geladen. Nur `core/geometry` importiert build123d.
4. **Picking funktioniert** (`vtkCellPicker`). Achtung beim Testen: Einen Punkt auf der *sichtbaren* Fläche projizieren, nicht die Körpermitte – durch die Perspektive trifft man sonst das Nachbarteil.
5. **slvs funktioniert**, hat aber eine gefährliche Stelle: Ein 2D-Punkt hat 4 Parameter-Einträge, nur die ersten 2 sind gültig. Das Lesen eines Platzhalters (0) **beendet den ganzen Prozess** (Assertion in C++, keine Python-Exception). → Der `SketchSolver`-Wrapper darf slvs-Handles nie nach außen geben.
6. **OCP 8 API**: `TopoDS.Face(...)` (ohne `_s`), aber `BRep_Tool.Triangulation_s(...)` – uneinheitlich, daher ebenfalls nur in `core/geometry` verwenden.
7. VTK hat keinen eingebauten „Zoom zum Mauszeiger“ → eigene Umsetzung im Viewport.
