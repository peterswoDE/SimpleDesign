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
from simpledesign.core.materials.library import DEFAULT_MATERIAL_ID, builtin_material
from simpledesign.core.materials.material import Material
from simpledesign.core.model.joint import Joint, JointKind, default_params
from simpledesign.core.model.part import (
    EDITABLE_PROPERTIES,
    PROPERTIES,
    Board,
    Group,
    Orientation,
    Parameter,
)
from simpledesign.core.model.settings import CutSettings
from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import (
    FUNCTIONS,
    Ref,
    format_number,
    is_plain_number,
    normalize_input,
    parse,
    references,
    replace_references,
)

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
        self.joints: dict[str, Joint] = {}
        self.cut_settings = CutSettings()

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
            if prop not in EDITABLE_PROPERTIES:
                raise KeyError(prop)
            self.boards[owner_id].expressions[prop] = stored

    def slots(self) -> Iterable[Slot]:
        """Every formula in the document."""
        for param_id in self.parameters:
            yield param_id, None
        for board in self.boards.values():
            for prop in EDITABLE_PROPERTIES:
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
        self.prune_materials()

    # ================================================================== duplicate
    def duplicate(self, ids: Iterable[str]) -> list[str]:
        """Copy boards/groups (with contents). Returns the IDs of the top-level copies.

        References *within* the copied set point to the copies, references to
        anything else stay as they are.
        """
        top = [i for i in ids if i in self.boards or i in self.groups]
        copied = self.with_contents(top)
        mapping = {old: new_id() for old in copied}

        def remap(stored: str) -> str:
            def replace(ref: Ref) -> str:
                target = ref_target_id(ref)
                if target in mapping:
                    return ".".join((f"@{mapping[target]}", *ref.path[1:]))
                return ".".join(ref.path)

            return replace_references(stored, replace)

        for old_id in [g for g in self.groups if g in copied]:
            group = copy.deepcopy(self.groups[old_id])
            group.id = mapping[old_id]
            group.parent_id = mapping.get(group.parent_id or "", group.parent_id)
            group.name = self.unique_name(group.name, is_parameter=False)
            self.groups[group.id] = group
        for old_id in [p for p in self.parameters if p in copied]:
            param = copy.deepcopy(self.parameters[old_id])
            param.id = mapping[old_id]
            param.group_id = mapping.get(param.group_id or "", param.group_id)
            param.expression = remap(param.expression)
            self.parameters[param.id] = param
        for old_id in [b for b in self.boards if b in copied]:
            board = copy.deepcopy(self.boards[old_id])
            board.id = mapping[old_id]
            board.group_id = mapping.get(board.group_id or "", board.group_id)
            board.name = self.unique_name(board.name, is_parameter=False)
            board.expressions = {k: remap(v) for k, v in board.expressions.items()}
            self.boards[board.id] = board
        # Joints between two copied boards are copied too.
        for joint in list(self.joints.values()):
            if joint.main_id in copied and joint.attached_id in copied:
                twin = copy.deepcopy(joint)
                twin.id = new_id()
                twin.main_id = mapping[joint.main_id]
                twin.attached_id = mapping[joint.attached_id]
                self.joints[twin.id] = twin
        return [mapping[i] for i in top]
