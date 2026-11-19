"""Variants: named sets of parameter values in one document.

A variant stores the formula of every parameter (stored form, references as
IDs). The parameters of the document always hold the *active* variant; the
others wait in ``doc.variants``. Switching saves the current values into the
active variant and loads the other one. Parameters a variant does not know
(added later) keep their current formula.
"""

from __future__ import annotations

import copy

from simpledesign.core.model.document import Document, ref_target_id
from simpledesign.core.params.errors import FormulaError
from simpledesign.core.params.expression import parse, references

FIRST_NAME = "Standard"


def snapshot(doc: Document) -> dict[str, str]:
    return {p.id: p.expression for p in doc.parameters.values()}


def synced(doc: Document) -> dict[str, dict[str, str]]:
    """All variants, the active one with the current parameter values."""
    result = {name: dict(values) for name, values in doc.variants.items()}
    if doc.active_variant is not None:
        result[doc.active_variant] = snapshot(doc)
    return result


def _usable(doc: Document, stored: str) -> bool:
    """The formula only refers to things that still exist."""
    try:
        tree = parse(stored)
    except FormulaError:
        return False
    for ref in references(tree):
        target = ref_target_id(ref)
        if target is not None and not (
            target in doc.parameters or target in doc.boards or target in doc.groups
        ):
            return False
    return True


def _apply(doc: Document, values: dict[str, str]) -> None:
    for param_id, stored in values.items():
        if param_id in doc.parameters and _usable(doc, stored):
            doc.parameters[param_id].expression = stored


def _check_name(doc: Document, name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("empty name")
    if name in doc.variants:
        raise ValueError("name already used")
    return name


def add(doc: Document, name: str) -> None:
    """New variant with the current values; it becomes the active one. The
    first time the current values are kept as ``Standard`` as well."""
    name = _check_name(doc, name)
    if not doc.variants:
        first = FIRST_NAME if name != FIRST_NAME else f"{FIRST_NAME}_1"
        doc.variants[first] = snapshot(doc)
        doc.active_variant = first
    doc.variants = synced(doc)
    doc.variants[name] = snapshot(doc)
    doc.active_variant = name


def switch(doc: Document, name: str) -> None:
    if name not in doc.variants:
        raise KeyError(name)
    if name == doc.active_variant:
        return
    doc.variants = synced(doc)
    _apply(doc, doc.variants[name])
    doc.active_variant = name


def rename(doc: Document, old: str, new: str) -> None:
    if old not in doc.variants:
        raise KeyError(old)
    if new.strip() == old:
        return
    new = _check_name(doc, new)
    doc.variants = {new if k == old else k: v for k, v in doc.variants.items()}
    if doc.active_variant == old:
        doc.active_variant = new


def remove(doc: Document, name: str) -> None:
    """Delete a variant; deleting the active one switches to the first other
    one. The last variant takes its values with it: they stay in the document."""
    if name not in doc.variants:
        raise KeyError(name)
    if name == doc.active_variant:
        others = [k for k in doc.variants if k != name]
        if others:
            switch(doc, others[0])
        else:
            doc.active_variant = None
    del doc.variants[name]


def variant_document(doc: Document, name: str) -> Document:
    """A copy of ``doc`` with variant ``name`` loaded (for exports)."""
    work = copy.deepcopy(doc)
    switch(work, name)
    return work
