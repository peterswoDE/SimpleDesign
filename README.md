# SimpleDesign

Einfaches parametrisches CAD-Tool für Holzmöbel ("Fusion lite").
Plan: [docs/PLAN.md](docs/PLAN.md) · Spike-Ergebnisse: [docs/spike-results.md](docs/spike-results.md)

## Start

```bash
uv sync
uv run python -m simpledesign
```

## Entwicklung

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

## Maus (wie Fusion)

| Aktion | Maus |
|---|---|
| Verschieben | mittlere Taste ziehen |
| Drehen | Shift + mittlere Taste ziehen |
| Zoomen | Mausrad (zum Cursor hin) |
| Auswählen | linke Taste (ab Phase 1) |
