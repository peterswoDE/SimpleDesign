"""Assembly instructions (Montageanleitung): the order to put the parts together.

The joints and fittings form a graph between the boards. Assembly starts
with the lowest, largest part and then adds, one after another, the part
that is joined to most of the parts already placed. A board with several
copies (a pattern) is one step. Parts without any joint come last.

Each step gets an isometric hidden-line picture: the parts placed so far
thin, the new part thick.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from simpledesign.core.drawing.projection import P2, STANDARD_VIEWS, Projection, ViewKind, project
from simpledesign.core.geometry.placement import world_bounds
from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.fitting import FittingKind
from simpledesign.core.model.joint import JointKind

if TYPE_CHECKING:
    from OCP.TopoDS import TopoDS_Shape


@dataclass(frozen=True, slots=True)
class Connection:
    """A joint or fitting that fixes the new part to one placed before."""

    id: str  # joint or fitting id
    kind: JointKind | FittingKind
    partner: str  # board id of the part already placed


@dataclass(slots=True)
class AssemblyStep:
    board_id: str
    copies: int
    connections: list[Connection] = field(default_factory=list)


def _links(doc: Document) -> list[tuple[str, JointKind | FittingKind, str, str]]:
    """(id, kind, board a, board b) of every joint and two-part fitting."""
    links: list[tuple[str, JointKind | FittingKind, str, str]] = []
    for joint in doc.joints.values():
        links.append((joint.id, joint.kind, joint.main_id, joint.attached_id))
    for fitting in doc.fittings.values():
        if fitting.carrier_id is not None:
            links.append((fitting.id, fitting.kind, fitting.carrier_id, fitting.part_id))
    return links


def assembly_steps(doc: Document, evaluation: Evaluation) -> list[AssemblyStep]:
    """The visible boards in assembly order."""
    boards = [b.id for b in doc.boards.values() if doc.is_visible(b)]
    parts = evaluation.machining.parts
    order = {board_id: i for i, board_id in enumerate(boards)}

    def bottom(board_id: str) -> float:
        lows = [world_bounds(s.origin, s.size, s.rotation)[0][2] for s in parts[board_id]]
        return min(lows, default=0.0)

    def area(board_id: str) -> float:
        sizes = sorted(parts[board_id][0].size) if parts[board_id] else [0.0, 0.0, 0.0]
        return sizes[1] * sizes[2]

    links = [link for link in _links(doc) if link[2] in order and link[3] in order]
    degree = dict.fromkeys(boards, 0)
    for _id, _kind, a, b in links:
        degree[a] += 1
        degree[b] += 1
    placed: list[str] = []
    done: set[str] = set()
    steps: list[AssemblyStep] = []
    while len(done) < len(boards):
        # parts joined to what is placed: the one with most joints there first
        candidates: dict[str, list[Connection]] = {}
        for link_id, kind, a, b in links:
            for new, old in ((a, b), (b, a)):
                if new not in done and old in done:
                    candidates.setdefault(new, []).append(Connection(link_id, kind, old))
        if candidates:
            board_id = min(
                candidates,
                key=lambda b: (-len({c.partner for c in candidates[b]}), bottom(b), order[b]),
            )
            connections = candidates[board_id]
        else:
            # start (again): the part most others are joined to, then the
            # lowest, largest one; parts without joints last
            rest = [b for b in boards if b not in done]
            board_id = min(
                rest,
                key=lambda b: (-degree[b], round(bottom(b), 1), -area(b), order[b]),
            )
            connections = []
        connections.sort(key=lambda c: placed.index(c.partner))
        steps.append(AssemblyStep(board_id, len(parts[board_id]), connections))
        placed.append(board_id)
        done.add(board_id)
    return steps


# ====================================================================== pictures
@dataclass(slots=True)
class StepPicture:
    """Lines of one step in view coordinates (mm): earlier parts thin, the new
    part thick; hidden edges of the new part dashed."""

    old: list[list[P2]] = field(default_factory=list)
    new: list[list[P2]] = field(default_factory=list)
    new_hidden: list[list[P2]] = field(default_factory=list)

    def bounds(self) -> tuple[P2, P2]:
        points = [p for line in self.old + self.new + self.new_hidden for p in line]
        if not points:
            return (0.0, 0.0), (0.0, 0.0)
        us = [p[0] for p in points]
        vs = [p[1] for p in points]
        return (min(us), min(vs)), (max(us), max(vs))


def _segments(lines: list[list[P2]]) -> list[tuple[P2, P2]]:
    return [(line[i], line[i + 1]) for line in lines for i in range(len(line) - 1)]


def _near(p: P2, a: P2, b: P2, tol: float) -> bool:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return math.dist(p, a) < tol
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy)) < tol


def _split(
    lines: list[list[P2]], own: list[tuple[P2, P2]], tol: float
) -> tuple[list[list[P2]], list[list[P2]]]:
    """Split ``lines`` into pieces on an edge of ``own`` and the others."""
    mine: list[list[P2]] = []
    others: list[list[P2]] = []
    for line in lines:
        for a, b in itertools.pairwise(line):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            hit = any(
                _near(mid, p, q, tol) and _near(a, p, q, tol) and _near(b, p, q, tol)
                for p, q in own
            )
            (mine if hit else others).append([a, b])
    return mine, others


def step_pictures(
    doc: Document, evaluation: Evaluation, steps: list[AssemblyStep]
) -> list[StepPicture]:
    """An isometric hidden-line picture per step."""
    from simpledesign.core.geometry.mesh import make_shape
    from simpledesign.core.geometry.placement import placed

    view = STANDARD_VIEWS[ViewKind.ISO]
    shapes: dict[str, list[TopoDS_Shape]] = {}
    for step in steps:
        shapes[step.board_id] = [
            placed(make_shape(part.size, part.features), part.origin, part.rotation)
            for part in evaluation.machining.parts[step.board_id]
        ]
    pictures = []
    so_far: list[TopoDS_Shape] = []
    for step in steps:
        new = shapes[step.board_id]
        so_far = so_far + new
        together = project(so_far, view, hidden=True)
        alone: Projection = project(new, view, hidden=True)
        own = _segments(alone.visible + alone.hidden)
        new_visible, old_visible = _split(together.visible, own, 0.05)
        new_hidden, _old_hidden = _split(together.hidden, own, 0.05)
        pictures.append(StepPicture(old_visible, new_visible, new_hidden))
    return pictures
