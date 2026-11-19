"""Exploded view (Explosionsansicht): pull the parts apart for display.

Every part moves away from the centre of the model. Parts in a top level
group move with their group first (so a cabinet stays together as a unit)
and then spread a little within it. The offsets only change the display;
the document is not touched.
"""

from __future__ import annotations

from simpledesign.core.geometry.placement import world_bounds
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation

type Vec3 = tuple[float, float, float]

INNER = 0.5  # how far parts spread within their group, relative to the groups


def _centre(lo: Vec3, hi: Vec3) -> Vec3:
    return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)


def _top_group(doc: Document, group_id: str | None) -> str | None:
    chain = doc.scope_chain(group_id)
    return chain[-2] if len(chain) > 1 else None


def explode_offsets(
    doc: Document,
    evaluation: Evaluation,
    factor: float,
    board_ids: set[str] | None = None,
) -> dict[str, list[Vec3]]:
    """Display offset of every part (copy) of ``board_ids`` (default: the
    visible boards) at ``factor`` (0 = assembled, 1 = parts twice as far
    from the centre as assembled)."""
    if board_ids is None:
        board_ids = {b.id for b in doc.boards.values() if doc.is_visible(b)}
    parts = evaluation.machining.parts
    centres: dict[str, list[Vec3]] = {}
    for board_id in board_ids:
        centres[board_id] = [
            _centre(*world_bounds(shape.origin, shape.size, shape.rotation))
            for shape in parts.get(board_id, [])
        ]
    every = [c for cs in centres.values() for c in cs]
    if not every or factor == 0:
        return {b: [(0.0, 0.0, 0.0)] * len(cs) for b, cs in centres.items()}

    def mean(points: list[Vec3]) -> Vec3:
        n = len(points)
        return (
            sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n,
        )

    model = mean(every)
    by_group: dict[str, list[Vec3]] = {}
    top: dict[str, str | None] = {}
    for board_id, cs in centres.items():
        group = _top_group(doc, doc.boards[board_id].group_id)
        top[board_id] = group
        if group is not None:
            by_group.setdefault(group, []).extend(cs)
    group_centres = {g: mean(cs) for g, cs in by_group.items()}

    result: dict[str, list[Vec3]] = {}
    for board_id, cs in centres.items():
        group = top[board_id]
        offsets = []
        for c in cs:
            if group is None:
                d = tuple((c[i] - model[i]) * factor for i in range(3))
            else:
                g = group_centres[group]
                d = tuple(
                    (g[i] - model[i]) * factor + (c[i] - g[i]) * factor * INNER for i in range(3)
                )
                if len(by_group) == 1:  # one cabinet: spread its parts fully
                    d = tuple((c[i] - g[i]) * factor for i in range(3))
            offsets.append((d[0], d[1], d[2]))
        result[board_id] = offsets
    return result
