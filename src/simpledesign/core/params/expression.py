"""Safe formula parser and evaluator (no ``eval``).

Grammar::

    expr    := term (("+" | "-") term)*
    term    := unary (("*" | "/") unary)*
    unary   := ("+" | "-") unary | primary
    primary := NUMBER | call | ref | "(" expr ")"
    call    := NAME "(" expr (";" expr)* ")"
    ref     := (NAME | ID) ("." NAME)*

* Decimal separator is the point; function arguments are separated by ``;``.
* Names may contain umlauts (``Höhe``).
* Stored formulas reference objects by ID: ``@<32 hex>`` or ``@<32 hex>.prop``.
  The user sees and types names; :mod:`simpledesign.core.model.symbols`
  converts between the two forms by replacing the source span of each reference.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from simpledesign.core.params.errors import ErrorCode, FormulaError

# --------------------------------------------------------------------------- AST


@dataclass(frozen=True, slots=True)
class Num:
    value: float


@dataclass(frozen=True, slots=True)
class Ref:
    """A reference like ``t``, ``Seite.x`` or (stored form) ``@abc….x``."""

    path: tuple[str, ...]
    start: int  # span in the source text, used to rewrite names <-> IDs
    end: int


@dataclass(frozen=True, slots=True)
class Unary:
    op: str
    operand: Node


@dataclass(frozen=True, slots=True)
class Binary:
    op: str
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    args: tuple[Node, ...]


type Node = Num | Ref | Unary | Binary | Call


def _round_half_up(x: float) -> float:
    # Python's round() rounds half to even (round(2.5) == 2) - surprising for users.
    return float(math.floor(x + 0.5))


# name -> (function, min args, max args or None for "any")
FUNCTIONS: dict[str, tuple[Callable[..., float], int, int | None]] = {
    "min": (min, 1, None),
    "max": (max, 1, None),
    "round": (_round_half_up, 1, 1),
    "floor": (lambda x: float(math.floor(x)), 1, 1),
    "ceil": (lambda x: float(math.ceil(x)), 1, 1),
    "abs": (abs, 1, 1),
}

# --------------------------------------------------------------------------- tokenizer

_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<number>\d+(?:\.\d*)?|\.\d+)
  | (?P<id>@[0-9a-f]{32})
  | (?P<name>[^\W\d]\w*)
  | (?P<op>[-+*/().;])
  | (?P<comma>,)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # number | id | name | op | end
    text: str
    start: int
    end: int


def _tokenize(text: str) -> Iterator[_Token]:
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise FormulaError(ErrorCode.SYNTAX, position=pos, text=text[pos])
        kind = match.lastgroup
        assert kind is not None
        if kind == "comma":
            raise FormulaError(ErrorCode.DECIMAL_COMMA, position=pos)
        if kind != "space":
            yield _Token(kind, match.group(), match.start(), match.end())
        pos = match.end()
    yield _Token("end", "", len(text), len(text))


# --------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = list(_tokenize(text))
        self.index = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def advance(self) -> _Token:
        token = self.current
        self.index += 1
        return token

    def accept(self, op: str) -> bool:
        if self.current.kind == "op" and self.current.text == op:
            self.index += 1
            return True
        return False

    def expect(self, op: str) -> None:
        if not self.accept(op):
            self.fail()

    def fail(self) -> None:
        token = self.current
        if token.kind == "end":
            raise FormulaError(ErrorCode.SYNTAX, position=token.start, text="")
        raise FormulaError(ErrorCode.SYNTAX, position=token.start, text=token.text)

    def parse(self) -> Node:
        if self.current.kind == "end":
            raise FormulaError(ErrorCode.EMPTY)
        node = self.expr()
        if self.current.kind != "end":
            self.fail()
        return node

    def expr(self) -> Node:
        node = self.term()
        while self.current.kind == "op" and self.current.text in "+-":
            op = self.advance().text
            node = Binary(op, node, self.term())
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.current.kind == "op" and self.current.text in "*/":
            op = self.advance().text
            node = Binary(op, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.current.kind == "op" and self.current.text in "+-":
            op = self.advance().text
            return Unary(op, self.unary())
        return self.primary()

    def primary(self) -> Node:
        token = self.current
        if token.kind == "number":
            self.advance()
            return Num(float(token.text))
        if self.accept("("):
            node = self.expr()
            self.expect(")")
            return node
        if token.kind == "name" and self.tokens[self.index + 1].text == "(":
            return self.call()
        if token.kind in ("name", "id"):
            return self.ref()
        self.fail()
        raise AssertionError("unreachable")

    def call(self) -> Node:
        name = self.advance().text
        self.expect("(")
        args = [self.expr()]
        while self.accept(";"):
            args.append(self.expr())
        self.expect(")")
        if name not in FUNCTIONS:
            raise FormulaError(ErrorCode.UNKNOWN_FUNCTION, name=name)
        _, min_args, max_args = FUNCTIONS[name]
        if len(args) < min_args or (max_args is not None and len(args) > max_args):
            raise FormulaError(ErrorCode.ARGUMENT_COUNT, name=name, count=len(args))
        return Call(name, tuple(args))

    def ref(self) -> Node:
        first = self.advance()
        path = [first.text]
        end = first.end
        while self.accept("."):
            if self.current.kind != "name":
                self.fail()
            token = self.advance()
            path.append(token.text)
            end = token.end
        return Ref(tuple(path), first.start, end)


def parse(text: str) -> Node:
    """Parse a formula. Raises :class:`FormulaError` with code SYNTAX/EMPTY/…"""
    return _Parser(text).parse()


# --------------------------------------------------------------------------- helpers


def references(node: Node) -> list[Ref]:
    """All references in source order."""
    match node:
        case Num():
            return []
        case Ref():
            return [node]
        case Unary(operand=operand):
            return references(operand)
        case Binary(left=left, right=right):
            return references(left) + references(right)
        case Call(args=args):
            return [ref for arg in args for ref in references(arg)]


def evaluate(node: Node, lookup: Callable[[Ref], float]) -> float:
    """Evaluate ``node``; ``lookup`` supplies the value of every reference."""
    match node:
        case Num(value=value):
            result = value
        case Ref():
            result = lookup(node)
        case Unary(op=op, operand=operand):
            value = evaluate(operand, lookup)
            result = -value if op == "-" else value
        case Binary(op=op, left=left, right=right):
            a = evaluate(left, lookup)
            b = evaluate(right, lookup)
            if op == "+":
                result = a + b
            elif op == "-":
                result = a - b
            elif op == "*":
                result = a * b
            else:
                if b == 0:
                    raise FormulaError(ErrorCode.DIVISION_BY_ZERO)
                result = a / b
        case Call(name=name, args=args):
            function = FUNCTIONS[name][0]
            result = float(function(*(evaluate(arg, lookup) for arg in args)))
    if not math.isfinite(result):
        raise FormulaError(ErrorCode.NOT_FINITE)
    return result


def replace_references(text: str, replace: Callable[[Ref], str]) -> str:
    """Return ``text`` with every reference replaced by ``replace(ref)``.

    Everything else (spacing, brackets, numbers) is kept exactly as typed.
    """
    refs = references(parse(text))
    parts: list[str] = []
    pos = 0
    for ref in sorted(refs, key=lambda r: r.start):
        parts.append(text[pos : ref.start])
        parts.append(replace(ref))
        pos = ref.end
    parts.append(text[pos:])
    return "".join(parts)


_PLAIN_NUMBER_RE = re.compile(r"^\s*([+-]?)\s*(\d+(?:[.,]\d+)?|[.,]\d+)\s*$")


def normalize_input(text: str) -> str:
    """Clean up user input: a plain number may use a decimal comma (``18,5``)."""
    match = _PLAIN_NUMBER_RE.match(text)
    if match is None:
        return text.strip()
    sign, digits = match.groups()
    return ("-" if sign == "-" else "") + digits.replace(",", ".")


def is_plain_number(text: str) -> bool:
    """True if ``text`` is a constant (``18``, ``-5.5``) rather than a formula."""
    try:
        node = parse(text)
    except FormulaError:
        return False
    if isinstance(node, Unary):
        node = node.operand
    return isinstance(node, Num)


def format_number(value: float) -> str:
    """Format a value for storing in a formula: ``18.0`` -> ``18``, max. 3 decimals."""
    text = f"{round(value, 3):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


_TRAILING_OFFSET_RE = re.compile(r"^(?P<head>.*\S)\s*(?P<sign>[+-])\s*(?P<num>\d+(?:\.\d+)?)\s*$")


def add_offset(text: str, delta: float) -> str:
    """Shift a formula by ``delta`` (used when dragging a part with the mouse).

    * ``"500"``             + 25 -> ``"525"``
    * ``"Seite.x + t"``     + 25 -> ``"Seite.x + t + 25"``
    * ``"Seite.x + t + 25"`` - 25 -> ``"Seite.x + t"``    (existing offset is merged)
    """
    if is_plain_number(text):
        return format_number(evaluate(parse(text), _no_refs) + delta)
    head, offset = text.strip(), 0.0
    match = _TRAILING_OFFSET_RE.match(head)
    if match is not None:
        candidate = match["head"]
        # "a * 5" must not be split, and "a * -5" leaves an invalid head -> parse check.
        try:
            parse(candidate)
        except FormulaError:
            pass
        else:
            head = candidate
            offset = float(match["num"]) * (-1 if match["sign"] == "-" else 1)
    offset += delta
    if abs(offset) < 1e-9:
        return head
    sign = "-" if offset < 0 else "+"
    return f"{head} {sign} {format_number(abs(offset))}"


def _no_refs(_ref: Ref) -> float:
    raise AssertionError("plain numbers have no references")
