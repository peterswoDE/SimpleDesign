from __future__ import annotations

import pytest

from simpledesign.core.params.errors import ErrorCode, FormulaError
from simpledesign.core.params.expression import (
    Ref,
    add_offset,
    evaluate,
    format_number,
    is_plain_number,
    normalize_input,
    parse,
    references,
    replace_references,
)

VALUES = {("H",): 1800.0, ("t",): 18.0, ("Höhe",): 10.0, ("Seite", "x"): 5.0, ("n",): 4.0}


def calc(text: str) -> float:
    return evaluate(parse(text), lambda ref: VALUES[ref.path])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("18", 18),
        ("18.5", 18.5),
        (".5", 0.5),
        ("H - 2 * t", 1764),
        ("(H - 2 * t) / 2", 882),
        ("-t", -18),
        ("--t", 18),
        ("10 - 4 - 3", 3),  # left associative
        ("24 / 4 / 2", 3),
        ("Höhe * 2", 20),
        ("Seite.x + t", 23),
        ("max(H; t; 3)", 1800),
        ("min(H; t)", 18),
        ("round(2.5)", 3),  # half up, not banker's rounding
        ("round(-2.5)", -2),
        ("floor(H / 7)", 257),
        ("ceil(1.1)", 2),
        ("abs(-t)", 18),
        ("(H - 2*t - n*t) / (n + 1)", (1800 - 36 - 72) / 5),
    ],
)
def test_evaluate(text: str, expected: float) -> None:
    assert calc(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", ErrorCode.EMPTY),
        ("   ", ErrorCode.EMPTY),
        ("H +", ErrorCode.SYNTAX),
        ("(H", ErrorCode.SYNTAX),
        ("H)", ErrorCode.SYNTAX),
        ("2t", ErrorCode.SYNTAX),
        ("H $ 2", ErrorCode.SYNTAX),
        ("Seite.", ErrorCode.SYNTAX),
        ("18,5 + t", ErrorCode.DECIMAL_COMMA),
        ("max(H, t)", ErrorCode.DECIMAL_COMMA),
        ("sqrt(4)", ErrorCode.UNKNOWN_FUNCTION),
        ("round(1; 2)", ErrorCode.ARGUMENT_COUNT),
    ],
)
def test_parse_errors(text: str, code: ErrorCode) -> None:
    with pytest.raises(FormulaError) as info:
        parse(text)
    assert info.value.code == code


def test_division_by_zero() -> None:
    with pytest.raises(FormulaError) as info:
        calc("H / (t - 18)")
    assert info.value.code == ErrorCode.DIVISION_BY_ZERO


def test_references_have_source_spans() -> None:
    text = "Seite.x + max(t; Höhe)"
    refs = references(parse(text))
    assert [r.path for r in refs] == [("Seite", "x"), ("t",), ("Höhe",)]
    assert [text[r.start : r.end] for r in refs] == ["Seite.x", "t", "Höhe"]


def test_stored_id_references() -> None:
    ref_id = "0123456789abcdef0123456789abcdef"
    (ref,) = references(parse(f"@{ref_id}.x * 2"))
    assert ref.path == (f"@{ref_id}", "x")


def test_replace_references_keeps_formatting() -> None:
    def upper(ref: Ref) -> str:
        return ".".join(p.upper() for p in ref.path)

    assert replace_references("(H  -2*t)/ Seite.x", upper) == "(H  -2*T)/ SEITE.X"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("18,5", "18.5"), (" 18 ", "18"), ("-3,25", "-3.25"), (",5", ".5"), ("H - 2", "H - 2")],
)
def test_normalize_input(text: str, expected: str) -> None:
    assert normalize_input(text) == expected


def test_is_plain_number() -> None:
    assert is_plain_number("18")
    assert is_plain_number("-18.5")
    assert not is_plain_number("2 * 9")
    assert not is_plain_number("t")
    assert not is_plain_number("H +")


@pytest.mark.parametrize(
    ("value", "expected"), [(18.0, "18"), (18.25, "18.25"), (1 / 3, "0.333"), (-0.0001, "0")]
)
def test_format_number(value: float, expected: str) -> None:
    assert format_number(value) == expected


@pytest.mark.parametrize(
    ("text", "delta", "expected"),
    [
        ("500", 25, "525"),
        ("-10", 10, "0"),
        ("Seite.x + t", 25, "Seite.x + t + 25"),
        ("Seite.x + t + 25", -25, "Seite.x + t"),
        ("Seite.x + t + 25", 5, "Seite.x + t + 30"),
        ("Seite.x - 10", 4, "Seite.x - 6"),
        ("Seite.x - 10", 15, "Seite.x + 5"),
        ("t * 5", 1, "t * 5 + 1"),  # "* 5" is not an offset
        ("t * -5", 1, "t * -5 + 1"),
    ],
)
def test_add_offset(text: str, delta: float, expected: str) -> None:
    assert add_offset(text, delta) == expected
