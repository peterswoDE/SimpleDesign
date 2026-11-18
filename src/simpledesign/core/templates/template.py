"""Templates: a saved group with its parameters.

A template is stored like a document (the ``.sdz`` JSON) that contains exactly
one top-level group plus a ``template`` entry with name and description. The
parameters *of that group* are the template's exposed parameters
(freigegebene Parameter): they are shown when inserting and keep controlling
the furniture afterwards. Built-in templates and the user's own ones ("save
group as template") use the same format.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from simpledesign.core.io import sdz
from simpledesign.core.model.document import Document
from simpledesign.core.model.part import Parameter

TEMPLATE_SUFFIX = ".sdt"
type Vec3 = tuple[float, float, float]


class TemplateError(Exception):
    """A template file is broken or does not contain exactly one group."""


@dataclass(frozen=True, slots=True)
class Template:
    name: str
    description: str
    data: sdz.Json  # the document dict (format of an .sdz file)
    builtin: bool = False
    path: Path | None = None  # file of a user template

    def document(self) -> Document:
        try:
            return sdz.from_dict(copy.deepcopy(self.data))
        except sdz.FileFormatError as error:
            raise TemplateError(str(error)) from error

    def root(self, doc: Document | None = None) -> str:
        """ID of the template's group in :meth:`document`."""
        doc = doc or self.document()
        roots = [g.id for g in doc.groups.values() if g.parent_id is None]
        loose = [b for b in doc.boards.values() if b.group_id is None]
        loose_params = [p for p in doc.parameters.values() if p.group_id is None]
        if len(roots) != 1 or loose or loose_params:
            raise TemplateError("a template must contain exactly one top-level group")
        return roots[0]

    def parameters(self) -> list[Parameter]:
        """The exposed parameters (display form of their formulas)."""
        doc = self.document()
        root = self.root(doc)
        result = []
        for param in doc.parameters.values():
            if param.group_id == root:
                text = doc.to_display(param.expression, root)
                result.append(Parameter(param.id, param.name, text, root))
        return result


def from_group(
    doc: Document, group_id: str, values: Mapping[str, float], name: str, description: str = ""
) -> Template:
    """Make a template from a group of ``doc``.

    Formulas inside the group that use anything outside it (global parameters,
    other parts) are frozen to their current value (``values``, by node key),
    exactly as if everything else had been deleted.
    """
    work = sdz.from_dict(sdz.to_dict(doc))
    # Detach first, so deleting the former parent groups does not take it along.
    work.groups[group_id].parent_id = None
    work.groups[group_id].visible = True
    keep = work.with_contents([group_id])
    others = [i for i in (*work.groups, *work.boards, *work.parameters) if i not in keep]
    work.delete(others, values)
    data = sdz.to_dict(work)
    data["template"] = {"name": name, "description": description}
    return Template(name, description, data)


def insert(
    doc: Document,
    template: Template,
    parent_id: str | None = None,
    parameters: Mapping[str, str] | None = None,
    offset: Vec3 = (0.0, 0.0, 0.0),
) -> str:
    """Insert ``template`` into ``doc``; returns the new group's ID.

    ``parameters`` overrides exposed parameters (name -> formula text, may
    only use other template parameters). Raises ``FormulaError`` for bad input.
    """
    source = template.document()
    root = template.root(source)
    for name, text in (parameters or {}).items():
        param = source.lookup_parameter(name, root)
        if param is None or param.group_id != root:
            raise KeyError(name)
        source.set_expression(param.id, None, text)
    (group_id,) = doc.copy_from(source, [root], parent_id)
    doc.translate([group_id], offset)
    return group_id


def save(template: Template, path: Path) -> None:
    data = dict(template.data)
    data["template"] = {"name": template.name, "description": template.description}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> Template:
    try:
        data = sdz.load_json(path)
        meta = data["template"]
        template = Template(str(meta["name"]), str(meta.get("description", "")), data, path=path)
    except (OSError, sdz.FileFormatError, KeyError, TypeError) as error:
        raise TemplateError(f"{path.name}: {error}") from error
    template.root()  # validates
    return template
