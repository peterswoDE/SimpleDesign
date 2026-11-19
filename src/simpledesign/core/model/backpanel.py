"""Back panels ("Rückwände").

A board marked as back panel sits a given distance in front of the rear edge
of the furniture (the back faces of the parts it is joined to). Other parts
stop at its front face - their depth is a formula up to the panel, so they
follow when the panel moves. A part marked "through the back panel" reaches
the rear edge instead and splits the panel into two, one on each side.

Back panels lie in a plane across their thickness axis; the furniture's
front is on the low side of that axis (Y points backwards).
"""

from __future__ import annotations

from collections.abc import Iterable

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation, Evaluator
from simpledesign.core.model.fit import AXES, Face, GrooveSettings, _Box, boxes, seat_in_groove
from simpledesign.core.model.joint import JointKind

EPS = 0.5  # mm: tolerance for "touching" and "covers"


def _box(doc: Document, evaluation: Evaluation, board_id: str) -> _Box | None:
    found = [b for b in boxes(doc, evaluation) if b.board_id == board_id]
    return found[0] if len(found) == 1 else None


def _partners(doc: Document, board_id: str) -> set[str]:
    return {
        j.main_id if j.attached_id == board_id else j.attached_id for j in doc.joints_of(board_id)
    }


def _axis(doc: Document, panel_id: str) -> int:
    return doc.boards[panel_id].orientation.thickness_axis.position


def rear_face(doc: Document, evaluation: Evaluation, panel_id: str) -> Face | None:
    """The rear edge behind the panel: the back face of the parts joined to it."""
    t = _axis(doc, panel_id)
    partners = _partners(doc, panel_id)
    near = [b for b in boxes(doc, evaluation) if b.board_id in partners]
    if not near:
        return None
    return max(near, key=lambda b: b.hi[t]).face(t, high=True)


def back_distance(doc: Document, evaluation: Evaluation, panel_id: str) -> float | None:
    """Distance from the rear edge to the back face of the panel."""
    rear = rear_face(doc, evaluation, panel_id)
    box = _box(doc, evaluation, panel_id)
    if rear is None or box is None:
        return None
    return rear.value - box.hi[_axis(doc, panel_id)]


def set_back_distance(
    doc: Document, evaluation: Evaluation, panel_id: str, distance: float
) -> None:
    """Place the panel ``distance`` in front of the rear edge (as a formula)."""
    rear = rear_face(doc, evaluation, panel_id)
    if rear is None:
        raise ValueError("back panel is not joined to any part")
    name = doc.boards[panel_id].name
    t = _axis(doc, panel_id)
    doc.set_expression(panel_id, "xyz"[t], f"{rear.formula} - {distance:g} - {name}.thickness")


def _overlap(a: _Box, b: _Box, axis: int) -> float:
    return min(a.hi[axis], b.hi[axis]) - max(a.lo[axis], b.lo[axis])


def _in_plane(t: int) -> list[int]:
    return [k for k in range(3) if k != t]


def stop_at_back(
    doc: Document, evaluation: Evaluation, panel_ids: Iterable[str] | None = None
) -> list[str]:
    """Parts in front of a back panel that reach into it (or touch it) end at
    its front face, linked by formula. Parts marked ``through_back``, other
    back panels and the parts the panel is joined to are left alone.
    Returns the changed board ids."""
    if panel_ids is None:
        panel_ids = [b.id for b in doc.boards.values() if b.back_panel]
    found = boxes(doc, evaluation)
    changed: list[str] = []
    for panel_id in panel_ids:
        panel = doc.boards[panel_id]
        pbox = _box(doc, evaluation, panel_id)
        if pbox is None:
            continue
        t = _axis(doc, panel_id)
        front = pbox.lo[t]
        partners = _partners(doc, panel_id)
        # A free (nailed-on) panel only shortens parts reaching into it; the
        # carcass edges that merely touch it stay as they are.
        reach = front - EPS if partners else front + EPS
        for box in found:
            board = doc.boards[box.board_id]
            if board.id == panel_id or board.back_panel or board.through_back:
                continue
            if board.id in partners or board.profile is not None:
                continue
            state = evaluation.boards[board.id]
            if state.count > 1 and state.pattern_axis == AXES[t]:
                continue
            if any(_overlap(box, pbox, k) <= EPS for k in _in_plane(t)):
                continue
            if not (box.lo[t] < front - EPS and box.hi[t] > reach):
                continue
            dim = board.orientation.dimension_along(AXES[t])
            axis = "xyz"[t]
            doc.set_expression(board.id, dim, f"{panel.name}.{axis} - {board.name}.{axis}")
            if board.id not in changed:
                changed.append(board.id)
    return changed


def _groove_of(doc: Document, panel_id: str) -> tuple[float, float]:
    """Depth and play of the panel's grooves (defaults if it has none)."""
    default = GrooveSettings()
    for joint in doc.joints_of(panel_id):
        if joint.kind == JointKind.GROOVE and joint.attached_id == panel_id:
            return (
                float(joint.params.get("depth", default.depth)),
                float(joint.params.get("play", default.play)),
            )
    return default.depth, default.play


def _drop_grooves(doc: Document, panel_id: str, partner: str | None = None) -> None:
    for joint in list(doc.joints_of(panel_id)):
        if joint.kind != JointKind.GROOVE or joint.attached_id != panel_id:
            continue
        if partner is None or joint.main_id == partner:
            del doc.joints[joint.id]


def _set_range(doc: Document, board_id: str, axis: int, lo: float, hi: float) -> None:
    board = doc.boards[board_id]
    dim = board.orientation.dimension_along(AXES[axis])
    doc.set_expression(board_id, "xyz"[axis], f"{lo:g}")
    doc.set_expression(board_id, dim, f"{hi - lo:g}")


def panels_behind(doc: Document, evaluation: Evaluation, part_id: str) -> list[str]:
    """Back panels behind ``part`` that it covers in their plane."""
    pbox = _box(doc, evaluation, part_id)
    if pbox is None:
        return []
    result = []
    for board in doc.boards.values():
        if not board.back_panel or board.id == part_id:
            continue
        box = _box(doc, evaluation, board.id)
        if box is None:
            continue
        t = _axis(doc, board.id)
        if pbox.lo[t] < box.lo[t] and all(_overlap(pbox, box, k) > EPS for k in _in_plane(t)):
            result.append(board.id)
    return result


def pass_through(doc: Document, evaluation: Evaluation, part_id: str) -> list[str]:
    """Let ``part`` reach the rear edge and split every back panel behind it
    into two, each fitted in grooves between the part and the other parts.
    Returns the new panel ids. Raises ValueError if that is not possible."""
    part = doc.boards[part_id]
    pbox = _box(doc, evaluation, part_id)
    panels = panels_behind(doc, evaluation, part_id)
    if pbox is None or not panels:
        raise ValueError("no back panel behind the part")
    plans = []
    for panel_id in panels:
        box = _box(doc, evaluation, panel_id)
        rear = rear_face(doc, evaluation, panel_id)
        distance = back_distance(doc, evaluation, panel_id)
        assert box is not None
        if rear is None or distance is None:
            raise ValueError("back panel is not joined to any part")
        t = _axis(doc, panel_id)
        s = part.orientation.thickness_axis.position
        if s == t:
            raise ValueError("part is parallel to the back panel")
        (o,) = (k for k in _in_plane(t) if k != s)
        if pbox.lo[o] > box.lo[o] + EPS or pbox.hi[o] < box.hi[o] - EPS:
            raise ValueError("part does not cross the whole back panel")
        if not (box.lo[s] + EPS < pbox.lo[s] and pbox.hi[s] < box.hi[s] - EPS):
            raise ValueError("part is not inside the back panel")
        plans.append((panel_id, box, rear, distance, t, s))

    _, _, rear, _, t, _ = plans[0]
    axis = "xyz"[t]
    dim = part.orientation.dimension_along(AXES[t])
    doc.set_expression(part_id, dim, f"{rear.formula} - {part.name}.{axis}")

    made: list[str] = []
    for panel_id, box, _rear, distance, _t, s in plans:
        depth, play = _groove_of(doc, panel_id)
        (copy,) = doc.duplicate([panel_id])
        _drop_grooves(doc, panel_id)
        _set_range(doc, panel_id, s, box.lo[s], pbox.lo[s])
        _set_range(doc, copy, s, pbox.hi[s], box.hi[s])
        groove = GrooveSettings(depth, play, distance)
        for board_id in (panel_id, copy):
            seat_in_groove(doc, Evaluator().evaluate(doc), board_id, groove)
        made.append(copy)
    return made


def join_back(doc: Document, evaluation: Evaluation, part_id: str) -> list[str]:
    """Undo :func:`pass_through`: merge the panels on both sides of ``part``
    again and let it stop at the merged panel. Returns the removed panels."""
    part = doc.boards[part_id]
    part.through_back = False
    s = part.orientation.thickness_axis.position
    pbox = _box(doc, evaluation, part_id)
    if pbox is None:
        return []
    joined = [
        j.attached_id
        for j in doc.joints_of(part_id)
        if j.main_id == part_id and doc.boards[j.attached_id].back_panel
    ]
    found = {i: _box(doc, evaluation, i) for i in joined}
    left = [i for i, b in found.items() if b is not None and b.hi[s] <= pbox.lo[s] + EPS]
    right = [i for i, b in found.items() if b is not None and b.lo[s] >= pbox.hi[s] - EPS]
    removed: list[str] = []
    seats: list[tuple[str, GrooveSettings]] = []
    front = None
    for keep in left:
        kbox = found[keep]
        assert kbox is not None
        t = _axis(doc, keep)
        twin = next(
            (
                i
                for i in right
                if (b := found[i]) is not None
                and abs(b.lo[t] - kbox.lo[t]) < EPS
                and _overlap(b, kbox, next(k for k in _in_plane(t) if k != s)) > EPS
            ),
            None,
        )
        if twin is None:
            continue
        tbox = found[twin]
        assert tbox is not None
        distance = back_distance(doc, evaluation, keep)
        depth, play = _groove_of(doc, keep)
        doc.delete([twin], dict(evaluation.values))
        right.remove(twin)
        removed.append(twin)
        _drop_grooves(doc, keep)
        _set_range(doc, keep, s, kbox.lo[s], tbox.hi[s])
        front = kbox.lo[t] if front is None else min(front, kbox.lo[t])
        seats.append((keep, GrooveSettings(depth, play, distance or 0.0)))
    if front is not None:
        t = _axis(doc, seats[0][0])
        dim = part.orientation.dimension_along(AXES[t])
        doc.set_expression(part_id, dim, f"{front - pbox.lo[t]:g}")
        for keep, groove in seats:
            seat_in_groove(doc, Evaluator().evaluate(doc), keep, groove)
        stop_at_back(doc, Evaluator().evaluate(doc), [keep for keep, _ in seats])
    return removed
