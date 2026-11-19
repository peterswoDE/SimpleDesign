"""Split parts where other parts pass through them ("Auftrennen").

The *cutters* stay whole. Every other part that a cutter crosses completely
(e.g. shelves crossed by a full-height divider) is split into pieces that end
at the cutter's faces. The piece ends are formulas, so the pieces follow when
the cutter moves; each piece is joined to the cutter.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluation
from simpledesign.core.model.fit import AXES, Box, boxes
from simpledesign.core.model.joint import Joint, JointKind
from simpledesign.core.params.expression import format_number

EPS = 0.5  # mm

Cut = tuple[float, float, str, str]  # lo, hi, lo formula, hi formula along the split axis


def _crosses(cutter: Box, box: Box, s: int) -> bool:
    """``cutter`` (thickness along ``s``) passes completely through ``box``."""
    if not (cutter.lo[s] > box.lo[s] + EPS and cutter.hi[s] < box.hi[s] - EPS):
        return False
    return all(
        cutter.lo[k] <= box.lo[k] + EPS and cutter.hi[k] >= box.hi[k] - EPS
        for k in range(3)
        if k != s
    )


def _cuts_for(doc: Document, cutters: list[Box], box: Box) -> tuple[int, list[Cut]] | None:
    """Split axis and sorted cuts of one board copy (None: not crossed)."""
    by_axis: dict[int, list[Cut]] = {}
    for c in cutters:
        s = doc.boards[c.board_id].orientation.thickness_axis.position
        if _crosses(c, box, s):
            by_axis.setdefault(s, []).append((c.lo[s], c.hi[s], c.lo_formula[s], c.hi_formula[s]))
    if not by_axis:
        return None
    s, cuts = max(by_axis.items(), key=lambda item: len(item[1]))
    cuts.sort()
    merged: list[Cut] = []
    for cut in cuts:
        if merged and cut[0] < merged[-1][1] - EPS:
            continue  # overlapping cutters: keep the first
        merged.append(cut)
    return s, merged


def _paren(text: str) -> str:
    return text if text.replace(".", "").replace("_", "").isalnum() else f"({text})"


def split_at(doc: Document, evaluation: Evaluation, cutter_ids: Iterable[str]) -> list[str]:
    """Split every part crossed by one of ``cutter_ids``. Returns the new pieces."""
    cutter_set = set(cutter_ids)
    found = boxes(doc, evaluation)
    cutters = [b for b in found if b.board_id in cutter_set]
    made: list[str] = []
    for board_id in list(doc.boards):
        board = doc.boards[board_id]
        if board_id in cutter_set or board.profile is not None:
            continue
        own = [b for b in found if b.board_id == board_id]
        if not own:
            continue
        plans = [_cuts_for(doc, cutters, b) for b in own]
        first = plans[0]
        if first is None:
            continue
        s, cuts = first
        # every copy of a pattern must be cut the same way
        if any(p is None or p[0] != s or len(p[1]) != len(cuts) for p in plans):
            continue
        made += _split(doc, evaluation, board_id, s, cuts, own[0], cutter_set)
    return made


def _split(
    doc: Document,
    evaluation: Evaluation,
    board_id: str,
    s: int,
    cuts: list[Cut],
    box: Box,
    cutter_set: set[str],
) -> list[str]:
    board = doc.boards[board_id]
    name = board.name
    axis = "xyz"[s]
    dim = board.orientation.dimension_along(AXES[s])
    state = evaluation.boards[board_id]
    x_text = doc.display_expression(board_id, axis)
    dim_text = doc.display_expression(board_id, dim)
    if f"{name}." in x_text:  # the start depends on the part itself: freeze it
        x_text = format_number(state.origin[s])
        doc.set_expression(board_id, axis, x_text)
    if f"{name}." in dim_text:
        end = format_number(box.hi[s])
    else:
        end = f"{_paren(x_text)} + {_paren(dim_text)}"

    # joints with the cutters are replaced by the new ones below
    old_joints = [
        j for j in doc.joints_of(board_id) if not ({j.main_id, j.attached_id} & cutter_set)
    ]
    kinds = Counter(
        j.kind
        for j in doc.joints_of(board_id)
        if j.attached_id == board_id and j.kind != JointKind.GROOVE
    )
    kind = kinds.most_common(1)[0][0] if kinds else JointKind.DOWEL
    cutter_of = {
        (c[2], c[3]): next(
            b.board_id
            for b in boxes(doc, evaluation)
            if b.board_id in cutter_set and (b.lo_formula[s], b.hi_formula[s]) == (c[2], c[3])
        )
        for c in cuts
    }
    for joint in doc.joints_of(board_id):
        if {joint.main_id, joint.attached_id} & cutter_set:
            del doc.joints[joint.id]

    pieces = [board_id] + [doc.duplicate([board_id])[0] for _ in cuts]
    ranges: list[tuple[float, float]] = []
    lo_value = box.lo[s]
    for i, piece in enumerate(pieces):
        piece_name = doc.boards[piece].name
        if i > 0:
            doc.set_expression(piece, axis, cuts[i - 1][3])
            lo_value = cuts[i - 1][1]
        if i < len(cuts):
            doc.set_expression(piece, dim, f"{cuts[i][2]} - {piece_name}.{axis}")
            hi_value = cuts[i][0]
        else:
            doc.set_expression(piece, dim, f"{end} - {piece_name}.{axis}")
            hi_value = box.hi[s]
        ranges.append((lo_value, hi_value))

    # existing joints go to the pieces their partner touches
    partner_boxes = boxes(doc, evaluation)
    for joint in old_joints:
        partner = joint.main_id if joint.attached_id == board_id else joint.attached_id
        near = [b for b in partner_boxes if b.board_id == partner]
        touching = [
            piece
            for piece, (lo, hi) in zip(pieces, ranges, strict=True)
            if any(b.hi[s] >= lo - EPS and b.lo[s] <= hi + EPS for b in near)
        ]
        for piece in touching:
            if piece != board_id:
                _copy_joint(doc, joint, board_id, piece)
        if board_id not in touching and joint.id in doc.joints:
            del doc.joints[joint.id]

    joined: set[tuple[str, str]] = set()
    for i, piece in enumerate(pieces):
        for cut in cuts[max(i - 1, 0) : i + 1]:
            pair = (cutter_of[(cut[2], cut[3])], piece)
            if pair not in joined:  # a pattern touches a piece with two copies
                joined.add(pair)
                doc.add_joint(kind, *pair)
    return pieces[1:]


def _copy_joint(doc: Document, joint: Joint, old: str, new: str) -> None:
    main = new if joint.main_id == old else joint.main_id
    attached = new if joint.attached_id == old else joint.attached_id
    twin = doc.add_joint(joint.kind, main, attached)
    twin.params = dict(joint.params)
