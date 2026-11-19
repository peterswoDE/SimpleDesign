"""The document: all parameters, groups, boards and the materials they use.

All structural changes go through methods of :class:`Document` so that names,
IDs and formulas stay consistent. The UI never edits the dicts directly.

Name rules
----------
* Names must be valid formula identifiers (``Seite_links``, ``Höhe``).
* Boards and groups share one global namespace (``Seite.x``, ``Korpus.H``).
* A parameter name must be unique among all parameters that can see each
  other (its group, the enclosing groups, the global scope and sub-groups).
  So there is no shadowing and a bare name always means exactly one parameter.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping

from simpledesign.core.ids import new_id
from simpledesign.core.materials.edgeband import BandEdge, EdgeBand, builtin_edge_band
from simpledesign.core.materials.library import DEFAULT_MATERIAL_ID, builtin_material
from simpledesign.core.materials.material import Material
from simpledesign.core.model import fitting as fittings
from simpledesign.core.model.fitting import Fitting, FittingKind
from simpledesign.core.model.joint import Joint, JointKind, default_params
from simpledesign.core.model.part import (
    EDITABLE_PROPERTIES,
    POSITION,
    PROPERTIES,
    Board,
    Group,
    Orientation,
    Parameter,
    SketchCut,
    is_sketch_property,
)
from simpledesign.core.model.settings import CutSettings
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import (
    FUNCTIONS,
    Ref,
    add_offset,
    format_number,
    is_plain_number,
    normalize_input,
    parse,
    references,
    replace_references,
)
from simpledesign.core.sketch.model import Sketch

NAME_RE = re.compile(r"^[^\W\d]\w*$")

type Owner = Parameter | Board
# Address of one formula: (owner id, property) - property is None for parameters.
type Slot = tuple[str, str | None]


def node_key(owner_id: str, prop: str | None = None) -> str:
    """Key of a value in the dependency graph: ``<param id>`` or ``<board id>.<prop>``."""
    return owner_id if prop is None else f"{owner_id}.{prop}"


def ref_key(ref: Ref) -> str:
    """Graph key of a *stored* reference (``@id`` / ``@id.prop``)."""
    return ".".join((ref.path[0].removeprefix("@"), *ref.path[1:]))


# Board properties that change when the board is moved.
_MOVING_PROPERTIES = frozenset((*POSITION, "x2", "y2", "z2"))


def ref_target_id(ref: Ref) -> str | None:
    first = ref.path[0]
    return first[1:] if first.startswith("@") else None


class Document:
    def __init__(self) -> None:
        self.parameters: dict[str, Parameter] = {}
        self.groups: dict[str, Group] = {}
        self.boards: dict[str, Board] = {}
        # Copies of all materials used - the file stays readable without the library.
        self.materials: dict[str, Material] = {}
        # Own edge bands and copies loaded from a file; built-ins are found anyway.
        self.edge_bands: dict[str, EdgeBand] = {}
        self.joints: dict[str, Joint] = {}
        self.fittings: dict[str, Fitting] = {}
        self.cut_settings = CutSettings()
        # Variants: name -> stored formula per parameter id (see variants.py).
        self.variants: dict[str, dict[str, str]] = {}
        self.active_variant: str | None = None

    # ================================================================== lookup
    def name_of(self, obj_id: str) -> str:
        for table in (self.parameters, self.groups, self.boards):
            if obj_id in table:
                return table[obj_id].name
        raise KeyError(obj_id)

    def board_or_group_named(self, name: str) -> Board | Group | None:
        for board in self.boards.values():
            if board.name == name:
                return board
        for group in self.groups.values():
            if group.name == name:
                return group
        return None

    def scope_chain(self, group_id: str | None) -> list[str | None]:
        """``group_id``, its parent groups, then ``None`` (global)."""
        chain: list[str | None] = []
        current = group_id
        while current is not None:
            chain.append(current)
            current = self.groups[current].parent_id
        chain.append(None)
        return chain

    def subgroups(self, group_id: str) -> set[str]:
        """All groups below ``group_id`` (not including itself)."""
        result: set[str] = set()
        stack = [group_id]
        while stack:
            parent = stack.pop()
            for group in self.groups.values():
                if group.parent_id == parent and group.id not in result:
                    result.add(group.id)
                    stack.append(group.id)
        return result

    def lookup_parameter(self, name: str, scope: str | None) -> Parameter | None:
        """The parameter a bare ``name`` means inside ``scope``."""
        for group_id in self.scope_chain(scope):
            for param in self.parameters.values():
                if param.group_id == group_id and param.name == name:
                    return param
        return None

    def scope_of(self, owner_id: str) -> str | None:
        if owner_id in self.parameters:
            return self.parameters[owner_id].group_id
        return self.boards[owner_id].group_id

    def is_visible(self, board: Board) -> bool:
        """Visible itself and all enclosing groups visible."""
        return board.visible and all(
            self.groups[g].visible for g in self.scope_chain(board.group_id) if g is not None
        )

    # ================================================================== names
    def check_name(
        self,
        name: str,
        *,
        is_parameter: bool,
        group_id: str | None = None,
        exclude_id: str | None = None,
    ) -> None:
        if not NAME_RE.match(name) or name in FUNCTIONS:
            raise FormulaError(ErrorCode.INVALID_NAME, name=name)
        if is_parameter:
            # No shadowing: the name must be free in the enclosing scopes and below.
            visible_scopes = set(self.scope_chain(group_id))
            if group_id is None:
                visible_scopes |= set(self.groups)
            else:
                visible_scopes |= self.subgroups(group_id)
            clash = any(
                p.name == name and p.group_id in visible_scopes and p.id != exclude_id
                for p in self.parameters.values()
            )
        else:
            other = self.board_or_group_named(name)
            clash = other is not None and other.id != exclude_id
        if clash:
            raise FormulaError(ErrorCode.DUPLICATE_NAME, name=name)

    def unique_name(self, base: str, *, is_parameter: bool, group_id: str | None = None) -> str:
        """``base`` if free, else ``base_2``, ``base_3`` …"""
        stem = re.sub(r"_\d+$", "", base)
        candidates = [base] + [f"{stem}_{i}" for i in range(2, 10_000)]
        for name in candidates:
            try:
                self.check_name(name, is_parameter=is_parameter, group_id=group_id)
            except FormulaError:
                continue
            return name
        raise RuntimeError("no free name")  # pragma: no cover

    # ================================================================== formulas
    def to_stored(self, text: str, scope: str | None) -> str:
        """Convert user input (names) to the stored form (IDs).

        Raises :class:`FormulaError` for syntax errors and unknown names - such
        input is rejected immediately and never stored.
        """
        text = normalize_input(text)

        def resolve(ref: Ref) -> str:
            path = ref.path
            full = ".".join(path)
            if path[0].startswith("@"):  # already stored form
                target = path[0][1:]
                if target not in self.parameters and target not in self.boards:
                    raise FormulaError(ErrorCode.UNKNOWN_NAME, name=full)
                return full
            if len(path) == 1:
                param = self.lookup_parameter(path[0], scope)
                if param is None:
                    raise FormulaError(ErrorCode.UNKNOWN_NAME, name=full)
                return f"@{param.id}"
            if len(path) == 2:
                obj = self.board_or_group_named(path[0])
                if isinstance(obj, Board):
                    if path[1] not in PROPERTIES:
                        raise FormulaError(ErrorCode.UNKNOWN_PROPERTY, name=full)
                    return f"@{obj.id}.{path[1]}"
                if isinstance(obj, Group):
                    for param in self.parameters.values():
                        if param.group_id == obj.id and param.name == path[1]:
                            return f"@{param.id}"
            raise FormulaError(ErrorCode.UNKNOWN_NAME, name=full)

        parse(text)  # raises EMPTY/SYNTAX before any name lookup
        return replace_references(text, resolve)

    def to_display(self, stored: str, scope: str | None) -> str:
        """Convert a stored formula to what the user sees (names)."""

        def show(ref: Ref) -> str:
            target = ref_target_id(ref)
            if target in self.parameters:
                param = self.parameters[target]
                if self.lookup_parameter(param.name, scope) is param:
                    return param.name
                assert param.group_id is not None  # globals are always visible
                return f"{self.groups[param.group_id].name}.{param.name}"
            if target in self.boards:
                return ".".join((self.boards[target].name, *ref.path[1:]))
            return ".".join(ref.path)

        try:
            return replace_references(stored, show)
        except FormulaError:
            return stored

    def stored_expression(self, owner_id: str, prop: str | None = None) -> str:
        if prop is None:
            return self.parameters[owner_id].expression
        return self.boards[owner_id].expressions[prop]

    def display_expression(self, owner_id: str, prop: str | None = None) -> str:
        return self.to_display(self.stored_expression(owner_id, prop), self.scope_of(owner_id))

    def set_expression(self, owner_id: str, prop: str | None, text: str) -> None:
        """Set a formula from user input (names). Raises FormulaError on bad input."""
        stored = self.to_stored(text, self.scope_of(owner_id))
        self.set_stored_expression(owner_id, prop, stored)

    def set_stored_expression(self, owner_id: str, prop: str | None, stored: str) -> None:
        if prop is None:
            self.parameters[owner_id].expression = stored
        else:
            if prop not in EDITABLE_PROPERTIES and not is_sketch_property(prop):
                raise KeyError(prop)
            self.boards[owner_id].expressions[prop] = stored

    def slots(self) -> Iterable[Slot]:
        """Every formula in the document."""
        for param_id in self.parameters:
            yield param_id, None
        for board in self.boards.values():
            for prop in board.formula_properties():
                yield board.id, prop

    # ================================================================== create
    def add_parameter(self, name: str, text: str, group_id: str | None = None) -> Parameter:
        self.check_name(name, is_parameter=True, group_id=group_id)
        stored = self.to_stored(text, group_id)
        param = Parameter(new_id(), name, stored, group_id)
        self.parameters[param.id] = param
        return param

    def add_group(self, name: str | None = None, parent_id: str | None = None) -> Group:
        name = name or self.unique_name("Gruppe_1", is_parameter=False)
        self.check_name(name, is_parameter=False)
        group = Group(new_id(), name, parent_id)
        self.groups[group.id] = group
        return group

    def add_board(
        self,
        name: str | None = None,
        group_id: str | None = None,
        material_id: str | Material = DEFAULT_MATERIAL_ID,
        orientation: Orientation = Orientation.XY,
    ) -> Board:
        name = name or self.unique_name("Brett_1", is_parameter=False)
        self.check_name(name, is_parameter=False)
        first_id = material_id if isinstance(material_id, str) else material_id.id
        board = Board(new_id(), name, first_id, group_id, orientation)
        self.boards[board.id] = board
        self.set_material(board.id, material_id)
        return board

    # ================================================================== edit
    def rename(self, obj_id: str, name: str) -> None:
        """Rename anything. Formulas store IDs, so nothing breaks."""
        if obj_id in self.parameters:
            param = self.parameters[obj_id]
            self.check_name(name, is_parameter=True, group_id=param.group_id, exclude_id=obj_id)
            param.name = name
        elif obj_id in self.boards or obj_id in self.groups:
            self.check_name(name, is_parameter=False, exclude_id=obj_id)
            (self.boards.get(obj_id) or self.groups[obj_id]).name = name
        else:
            raise KeyError(obj_id)

    def set_material(self, board_id: str, material: str | Material) -> None:
        """Assign a material; a plain-number thickness follows the material.

        ``material`` is a library material (copied into the document unless the
        document already has its own copy of that ID) or the ID of a material
        the document already contains / a built-in one.
        """
        if isinstance(material, Material):
            self.materials.setdefault(material.id, material)
            material_id = material.id
        else:
            material_id = material
            if material_id not in self.materials:
                self.materials[material_id] = builtin_material(material_id)
        board = self.boards[board_id]
        board.material_id = material_id
        self._apply_material_size(board, self.materials[material_id])
        self.prune_materials()

    def _apply_material_size(self, board: Board, material: Material) -> None:
        if is_plain_number(board.expressions["thickness"]):
            board.expressions["thickness"] = format_number(material.thickness)
        if material.width is not None and is_plain_number(board.expressions["width"]):
            board.expressions["width"] = format_number(material.width)

    def update_material(self, material: Material) -> None:
        """Replace the document's copy of a material (e.g. from the library)."""
        if material.id not in self.materials:
            raise KeyError(material.id)
        self.materials[material.id] = material
        for board in self.boards.values():
            if board.material_id == material.id:
                self._apply_material_size(board, material)

    def has_grain(self, board_id: str) -> bool:
        """Must the part's length follow the grain of the stock?"""
        board = self.boards[board_id]
        return self.materials[board.material_id].has_grain and not board.ignore_grain

    def prune_materials(self) -> None:
        used = {board.material_id for board in self.boards.values()}
        for material_id in list(self.materials):
            if material_id not in used:
                del self.materials[material_id]

    def edge_band(self, band_id: str) -> EdgeBand:
        """The document's band of that ID, else the built-in one (KeyError: unknown)."""
        band = self.edge_bands.get(band_id) or builtin_edge_band(band_id)
        if band is None:
            raise KeyError(band_id)
        return band

    def set_edge_band(
        self, board_id: str, edges: Iterable[BandEdge], band: EdgeBand | str | None
    ) -> None:
        """Band ``edges`` of the board with ``band`` (None: remove the banding)."""
        if isinstance(band, EdgeBand):
            if builtin_edge_band(band.id) != band:
                self.edge_bands[band.id] = band
            band = band.id
        elif band is not None:
            self.edge_band(band)  # KeyError for unknown bands
        board = self.boards[board_id]
        for edge in edges:
            if band is None:
                board.edges.pop(edge, None)
            else:
                board.edges[edge] = band

    def move_to_group(self, ids: Iterable[str], group_id: str | None) -> None:
        """Move boards, groups or parameters into ``group_id`` (None = top level)."""
        ids = list(ids)
        for obj_id in ids:
            if (
                obj_id in self.groups
                and group_id is not None
                and (group_id == obj_id or group_id in self.subgroups(obj_id))
            ):
                raise ValueError("a group cannot be moved into itself")
        # Parameters must not clash with names visible at the new place.
        old = {i: self._parent_of(i) for i in ids}
        try:
            for obj_id in ids:
                self._set_parent(obj_id, group_id)
            for param in self.parameters.values():
                self.check_name(
                    param.name, is_parameter=True, group_id=param.group_id, exclude_id=param.id
                )
        except FormulaError:
            for obj_id, parent in old.items():
                self._set_parent(obj_id, parent)
            raise

    def _parent_of(self, obj_id: str) -> str | None:
        if obj_id in self.groups:
            return self.groups[obj_id].parent_id
        if obj_id in self.boards:
            return self.boards[obj_id].group_id
        return self.parameters[obj_id].group_id

    def _set_parent(self, obj_id: str, parent: str | None) -> None:
        if obj_id in self.groups:
            self.groups[obj_id].parent_id = parent
        elif obj_id in self.boards:
            self.boards[obj_id].group_id = parent
        else:
            self.parameters[obj_id].group_id = parent

    # ================================================================== joints
    def add_joint(self, kind: JointKind, main_id: str, attached_id: str) -> Joint:
        if main_id not in self.boards or attached_id not in self.boards:
            raise KeyError("a joint needs two boards")
        if main_id == attached_id:
            raise ValueError("a joint needs two different boards")
        joint = Joint(new_id(), kind, main_id, attached_id, default_params(kind))
        self.joints[joint.id] = joint
        return joint

    def set_joint_param(self, joint_id: str, name: str, value: float) -> None:
        joint = self.joints[joint_id]
        if name not in default_params(joint.kind):
            raise KeyError(name)
        joint.params[name] = float(value)

    def swap_joint(self, joint_id: str) -> None:
        """Exchange main and attached part."""
        joint = self.joints[joint_id]
        joint.main_id, joint.attached_id = joint.attached_id, joint.main_id

    def joints_of(self, board_id: str) -> list[Joint]:
        return [j for j in self.joints.values() if board_id in (j.main_id, j.attached_id)]

    # ================================================================== fittings
    def add_fitting(
        self, kind: FittingKind, part_id: str, carrier_id: str | None = None, side: int = 1
    ) -> Fitting:
        if part_id not in self.boards or (carrier_id is not None and carrier_id not in self.boards):
            raise KeyError("a fitting needs existing boards")
        if part_id == carrier_id:
            raise ValueError("a fitting needs two different boards")
        if (carrier_id is None) != (kind == FittingKind.SHELF_PINS):
            raise ValueError("only shelf pin rows have no carrier")
        fitting = Fitting(
            new_id(),
            kind,
            part_id,
            carrier_id,
            1 if side >= 0 else -1,
            fittings.default_params(kind),
        )
        self.fittings[fitting.id] = fitting
        return fitting

    def set_fitting_param(self, fitting_id: str, name: str, value: float) -> None:
        fitting = self.fittings[fitting_id]
        if name not in fittings.default_params(fitting.kind):
            raise KeyError(name)
        fitting.params[name] = float(value)

    def fittings_of(self, board_id: str) -> list[Fitting]:
        return [f for f in self.fittings.values() if board_id in f.board_ids()]

    # ================================================================== delete
    def with_contents(self, ids: Iterable[str]) -> set[str]:
        """``ids`` plus everything inside the groups among them."""
        result = set(ids)
        for obj_id in list(result):
            if obj_id in self.groups:
                result |= self.subgroups(obj_id)
        groups = {g for g in result if g in self.groups}
        result |= {b.id for b in self.boards.values() if b.group_id in groups}
        result |= {p.id for p in self.parameters.values() if p.group_id in groups}
        return result

    def external_references(self, ids: Iterable[str]) -> list[Slot]:
        """Formulas *outside* ``ids`` (incl. contents) that reference something inside."""
        removed = self.with_contents(ids)
        result: list[Slot] = []
        for owner_id, prop in self.slots():
            if owner_id in removed:
                continue
            refs = references(parse(self.stored_expression(owner_id, prop)))
            if any(ref_target_id(ref) in removed for ref in refs):
                result.append((owner_id, prop))
        return result

    def delete(self, ids: Iterable[str], values: Mapping[str, float]) -> None:
        """Delete objects (and group contents).

        Formulas elsewhere that reference deleted objects are *frozen*: each such
        reference is replaced by its last value (``values``, keyed by node key).
        """
        removed = self.with_contents(ids)
        for owner_id, prop in self.external_references(removed):

            def freeze(ref: Ref) -> str:
                if ref_target_id(ref) in removed:
                    return format_number(values.get(ref_key(ref), 0.0))
                return ".".join(ref.path)

            stored = replace_references(self.stored_expression(owner_id, prop), freeze)
            self.set_stored_expression(owner_id, prop, stored)
        for obj_id in removed:
            self.parameters.pop(obj_id, None)
            self.groups.pop(obj_id, None)
            self.boards.pop(obj_id, None)
            self.joints.pop(obj_id, None)
        # Joints of deleted boards go with them.
        for joint in list(self.joints.values()):
            if joint.main_id not in self.boards or joint.attached_id not in self.boards:
                del self.joints[joint.id]
        # So do their fittings.
        for obj_id in removed:
            self.fittings.pop(obj_id, None)
        for fitting in list(self.fittings.values()):
            if any(b not in self.boards for b in fitting.board_ids()):
                del self.fittings[fitting.id]
        self.prune_materials()

    # ================================================================== duplicate
    def duplicate(self, ids: Iterable[str]) -> list[str]:
        """Copy boards/groups (with contents). Returns the IDs of the top-level copies.

        References *within* the copied set point to the copies, references to
        anything else stay as they are.
        """
        top = [i for i in ids if i in self.boards or i in self.groups]
        return self.copy_from(self, top, keep_parent=True)

    def copy_from(
        self,
        source: Document,
        top: Iterable[str],
        parent_id: str | None = None,
        *,
        keep_parent: bool = False,
    ) -> list[str]:
        """Copy boards/groups (with contents) from ``source`` (may be ``self``).

        ``parent_id`` is the group the top-level copies go into; ``keep_parent``
        keeps their current group instead (only sensible when ``source`` is ``self``). Names
        are made unique, materials are taken over if this document lacks them.
        Returns the IDs of the top-level copies.
        """
        top = list(top)
        copied = source.with_contents(top)
        mapping = {old: new_id() for old in copied}

        def remap(stored: str) -> str:
            def replace(ref: Ref) -> str:
                target = ref_target_id(ref)
                if target in mapping:
                    return ".".join((f"@{mapping[target]}", *ref.path[1:]))
                return ".".join(ref.path)

            return replace_references(stored, replace)

        def new_parent(old: str | None, obj_id: str) -> str | None:
            if obj_id in top and not keep_parent:
                return parent_id
            return mapping.get(old or "", old)

        for old_id in [g for g in source.groups if g in copied]:
            group = copy.deepcopy(source.groups[old_id])
            group.id = mapping[old_id]
            group.parent_id = new_parent(group.parent_id, old_id)
            group.name = self.unique_name(group.name, is_parameter=False)
            self.groups[group.id] = group
        for old_id in [p for p in source.parameters if p in copied]:
            param = copy.deepcopy(source.parameters[old_id])
            param.id = mapping[old_id]
            param.group_id = mapping.get(param.group_id or "", param.group_id)
            param.expression = remap(param.expression)
            param.name = self.unique_name(param.name, is_parameter=True, group_id=param.group_id)
            self.parameters[param.id] = param
        for old_id in [b for b in source.boards if b in copied]:
            board = copy.deepcopy(source.boards[old_id])
            board.id = mapping[old_id]
            board.group_id = new_parent(board.group_id, old_id)
            board.name = self.unique_name(board.name, is_parameter=False)
            board.expressions = {k: remap(v) for k, v in board.expressions.items()}
            if board.material_id not in self.materials:
                self.materials[board.material_id] = copy.deepcopy(
                    source.materials[board.material_id]
                )
            for band_id in board.edges.values():
                if band_id not in self.edge_bands and band_id in source.edge_bands:
                    self.edge_bands[band_id] = source.edge_bands[band_id]
            self.boards[board.id] = board
        # Joints between two copied boards are copied too.
        for joint in list(source.joints.values()):
            if joint.main_id in copied and joint.attached_id in copied:
                twin = copy.deepcopy(joint)
                twin.id = new_id()
                twin.main_id = mapping[joint.main_id]
                twin.attached_id = mapping[joint.attached_id]
                self.joints[twin.id] = twin
        # Fittings whose boards are all copied, too.
        for fitting in list(source.fittings.values()):
            if all(b in copied for b in fitting.board_ids()):
                copied_fitting = copy.deepcopy(fitting)
                copied_fitting.id = new_id()
                copied_fitting.part_id = mapping[fitting.part_id]
                if fitting.carrier_id is not None:
                    copied_fitting.carrier_id = mapping[fitting.carrier_id]
                self.fittings[copied_fitting.id] = copied_fitting
        return [mapping[i] for i in top]

    # ================================================================== sketches
    def _set_sketch_formulas(
        self, board: Board, old: Sketch | None, new: Sketch | None, formulas: Mapping[str, str]
    ) -> None:
        """Replace the dimension formulas of ``old`` by those of ``new``.

        ``formulas``: user input (names) per constraint id. Raises FormulaError.
        """
        for c in old.dimensions() if old is not None else []:
            board.expressions.pop(c.formula_key, None)
        for c in new.dimensions() if new is not None else []:
            self.set_expression(board.id, c.formula_key, formulas.get(c.id, "1"))

    def set_profile(
        self,
        board_id: str,
        sketch: Sketch | None,
        formulas: Mapping[str, str] | None = None,
        values: Mapping[str, float] | None = None,
    ) -> None:
        """Give a board a sketched outline (or remove it with ``sketch=None``).

        Removing it keeps the current size: ``values`` are the evaluated node values.
        """
        board = self.boards[board_id]
        if sketch is None and board.profile is not None and values is not None:
            for prop in ("length", "width"):
                value = values.get(f"{board_id}.{prop}")
                if value is not None:
                    board.expressions[prop] = format_number(value)
        self._set_sketch_formulas(board, board.profile, sketch, formulas or {})
        board.profile = None if sketch is None else sketch.copy()

    def set_cut(
        self, board_id: str, cut: SketchCut, formulas: Mapping[str, str], depth: str
    ) -> None:
        """Add a sketch cut-out or replace the one with the same id."""
        cut = copy.deepcopy(cut)
        board = self.boards[board_id]
        old = next((c for c in board.cuts if c.id == cut.id), None)
        self._set_sketch_formulas(board, old.sketch if old else None, cut.sketch, formulas)
        self.set_expression(board_id, cut.depth_key, depth)
        if old is None:
            board.cuts.append(cut)
        else:
            board.cuts[board.cuts.index(old)] = cut

    def delete_cut(self, board_id: str, cut_id: str) -> None:
        board = self.boards[board_id]
        cut = next(c for c in board.cuts if c.id == cut_id)
        self._set_sketch_formulas(board, cut.sketch, None, {})
        board.expressions.pop(cut.depth_key, None)
        board.cuts.remove(cut)

    # ================================================================== move
    def translate(self, ids: Iterable[str], delta: tuple[float, float, float]) -> None:
        """Move boards/groups (with contents) by ``delta``.

        Only *absolute* positions change: a position formula that (directly or
        through parameters) uses a position of another moved board follows by
        itself. All others get the offset added (numbers are added directly).
        """
        moved = self.with_contents(ids)
        boards = [b for b in self.boards.values() if b.id in moved]
        for axis, d in zip(POSITION, delta, strict=True):
            if abs(d) < 1e-12:
                continue
            for board in boards:
                stored = board.expressions[axis]
                if self._uses_boards(stored, moved, set()):
                    continue
                board.expressions[axis] = add_offset(stored, d)

    def _uses_boards(self, stored: str, boards: set[str], seen: set[str]) -> bool:
        for ref in references(parse(stored)):
            target = ref_target_id(ref)
            if target is None or target in seen:
                continue
            if target in boards and target in self.boards:
                # only positions follow a move - a size (e.g. the own
                # thickness in a centring formula) does not
                if ref.path[-1] in _MOVING_PROPERTIES:
                    return True
                continue
            if target in self.parameters:
                seen.add(target)
                if self._uses_boards(self.parameters[target].expression, boards, seen):
                    return True
        return False
