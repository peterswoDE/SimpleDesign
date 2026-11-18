"""Formula errors.

The core never produces user-facing text: an error carries a *code* plus
parameters, and the UI turns that into a translated message.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    EMPTY = "empty"
    SYNTAX = "syntax"
    DECIMAL_COMMA = "decimal_comma"  # "18,5" inside a formula
    UNKNOWN_NAME = "unknown_name"
    UNKNOWN_PROPERTY = "unknown_property"
    UNKNOWN_FUNCTION = "unknown_function"
    ARGUMENT_COUNT = "argument_count"
    DIVISION_BY_ZERO = "division_by_zero"
    NOT_FINITE = "not_finite"
    CYCLE = "cycle"
    NOT_POSITIVE = "not_positive"
    NOT_INTEGER = "not_integer"
    INVALID_NAME = "invalid_name"
    DUPLICATE_NAME = "duplicate_name"
    SKETCH_UNSOLVABLE = "sketch_unsolvable"  # contradicting sketch constraints
    PROFILE_INVALID = "profile_invalid"  # param problem: empty / open / branched


class FormulaError(Exception):
    """A formula or name could not be parsed, resolved or evaluated."""

    def __init__(self, code: ErrorCode, **params: object) -> None:
        super().__init__(code, params)
        self.code = code
        self.params = params

    def __str__(self) -> str:
        details = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
        return f"{self.code}({details})"
