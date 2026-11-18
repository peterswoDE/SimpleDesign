"""Phase 4: templates (Vorlagen)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simpledesign.core.model.document import Document
from simpledesign.core.model.evaluate import Evaluator
from simpledesign.core.model.machining import Cutout
from simpledesign.core.templates.builtin import bank, builtin_templates, korpus, regal, tisch
from simpledesign.core.templates.library import TemplateLibrary
from simpledesign.core.templates.template import (
    Template,
    TemplateError,
    from_group,
    insert,
    load,
)
from tests.furniture import board_id, build_korpus


def names(doc: Document) -> set[str]:
    return {b.name for b in doc.boards.values()}


@pytest.mark.parametrize("template", builtin_templates(), ids=lambda t: t.name)
def test_builtin_templates_are_valid(template: Template) -> None:
    doc = Document()
    group = insert(doc, template)
    ev = Evaluator().evaluate(doc)
    assert not ev.errors
    assert not ev.machining.errors
    assert all(b.group_id is not None for b in doc.boards.values())
    assert template.builtin
    assert {p.group_id for p in doc.parameters.values()} == {group}


def test_korpus_back_panel_sits_in_grooves() -> None:
    doc = Document()
    insert(doc, korpus())
    ev = Evaluator().evaluate(doc)
    back = ev.machining.parts[board_id(doc, "Rückwand")][0]
    # 1000 - 2*18 between the sides, + 2 * (8 - 0.5) in the grooves
    assert back.size[0] == pytest.approx(964 + 15)
    assert back.size[2] == pytest.approx(2000 - 36 + 15)
    for name in ("Seite_links", "Seite_rechts", "Boden", "Deckel"):
        features = ev.machining.parts[board_id(doc, name)][0].features
        assert sum(isinstance(f, Cutout) for f in features) == 1


def test_insert_with_parameters_and_offset() -> None:
    doc = Document()
    group = insert(doc, regal(), parameters={"B": "600", "n": "2"}, offset=(1000, 0, 50))
    ev = Evaluator().evaluate(doc)
    left, right = board_id(doc, "Seite_links"), board_id(doc, "Seite_rechts")
    assert ev.boards[left].origin == pytest.approx((1000, 0, 50))
    assert ev.boards[right].x == pytest.approx(1000 + 600 - 18)
    assert ev.boards[board_id(doc, "Fachboden")].count == 2
    # bottom is placed relative to the side - its formula was not changed
    assert doc.display_expression(board_id(doc, "Boden"), "x") == "Seite_links.x2"
    assert doc.groups[group].name == "Regal"
    with pytest.raises(KeyError):
        insert(doc, regal(), parameters={"nope": "1"})


def test_insert_twice_makes_names_unique() -> None:
    doc = Document()
    doc.add_parameter("H", "500")  # global H: the template's H must not shadow it
    first = insert(doc, regal())
    second = insert(doc, regal())
    assert doc.groups[second].name == "Regal_2"
    assert "Seite_links_2" in names(doc)
    params = {p.name for p in doc.parameters.values() if p.group_id == first}
    assert "H" not in params and "H_2" in params
    ev = Evaluator().evaluate(doc)
    assert not ev.errors
    # both shelves are 1800 high, not 500
    assert ev.boards[board_id(doc, "Seite_links_2")].length == pytest.approx(1800)


def test_template_parameters_keep_controlling() -> None:
    doc = Document()
    group = insert(doc, tisch())
    length = next(p for p in doc.parameters.values() if p.name == "L" and p.group_id == group)
    doc.set_expression(length.id, None, "2000")
    ev = Evaluator().evaluate(doc)
    assert not ev.machining.errors
    assert ev.boards[board_id(doc, "Tischplatte")].length == pytest.approx(2000)
    apron = ev.boards[board_id(doc, "Zarge_vorne")]
    assert apron.length == pytest.approx(2000 - 2 * 60 - 2 * 80)


def test_bench_joints() -> None:
    doc = Document()
    insert(doc, bank())
    ev = Evaluator().evaluate(doc)
    rail = ev.machining.parts[board_id(doc, "Traverse")][0]
    # the rail reaches 20 mm into both legs
    assert rail.size[0] == pytest.approx(ev.boards[board_id(doc, "Traverse")].length + 40)
    assert ev.machining.hardware


def test_group_as_template_freezes_outside_references(tmp_path: Path) -> None:
    doc = build_korpus()  # global parameters H, B, T, t, n
    group = doc.add_group("Korpus").id
    doc.move_to_group([b.id for b in doc.boards.values()], group)
    ev = Evaluator().evaluate(doc)
    template = from_group(doc, group, ev.values, "Mein Korpus", "Test")
    library = TemplateLibrary(tmp_path)
    path = library.add(template)
    assert path.name == "Mein Korpus.sdt"
    loaded = load(path)
    assert (loaded.name, loaded.description) == ("Mein Korpus", "Test")
    assert loaded.parameters() == []  # globals are not part of the template

    target = Document()
    insert(target, loaded)
    ev2 = Evaluator().evaluate(target)
    assert not ev2.errors
    assert ev2.boards[board_id(target, "Seite_links")].length == pytest.approx(1800)
    assert target.display_expression(board_id(target, "Seite_links"), "length") == "1800"

    listed = library.templates()
    assert [t.name for t in listed][-1] == "Mein Korpus"
    assert len(listed) == len(builtin_templates()) + 1
    library.remove(listed[-1])
    assert not path.exists()
    with pytest.raises(ValueError, match="built-in"):
        library.remove(listed[0])


def test_nested_group_as_template() -> None:
    doc = Document()
    outer = insert(doc, regal())
    inner = doc.add_group("Innen", parent_id=outer).id
    doc.add_parameter("p", "B / 2", inner)  # uses the outer group's parameter
    board = doc.add_board("Teil", inner)
    doc.set_expression(board.id, "length", "p")
    ev = Evaluator().evaluate(doc)
    template = from_group(doc, inner, ev.values, "Innen")
    (param,) = template.parameters()
    assert (param.name, param.expression) == ("p", "800 / 2")
    assert len(template.document().boards) == 1


def test_broken_template_files(tmp_path: Path) -> None:
    (tmp_path / "kaputt.sdt").write_text("{nope", encoding="utf-8")
    empty = Template("Leer", "", {"format_version": 3})
    with pytest.raises(TemplateError):
        empty.root()
    library = TemplateLibrary(tmp_path)
    assert len(library.templates()) == len(builtin_templates())
    assert len(library.errors) == 1
    with pytest.raises(TemplateError):
        load(tmp_path / "kaputt.sdt")


def test_translate_merges_offsets() -> None:
    doc = Document()
    group = insert(doc, regal())
    left = board_id(doc, "Seite_links")
    shelf = board_id(doc, "Fachboden")
    doc.translate([group], (0, 0, 100))
    doc.translate([group], (0, 0, -40))
    assert doc.display_expression(left, "z") == "60"
    # the shelf is placed from the bottom -> follows without change
    assert doc.display_expression(shelf, "z") == "Boden.z2 + Fachboden.spacing - t"
    # "x" of the shelf uses Seite_links -> unchanged; "y" is absolute
    doc.translate([group], (0, 25, 0))
    assert doc.display_expression(shelf, "x") == "Seite_links.x2 + 1"
    assert doc.display_expression(shelf, "y") == "25"
