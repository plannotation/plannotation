# SPDX-License-Identifier: Apache-2.0
"""Score an answer against the key: exact for words, within 1 % for numbers.

A model answers in prose conventions -- ``1:50``, ``8,000``, ``8 m``, ``"A, B, 1
and 2"`` -- and the key is a bare value. The parsing here is deliberately narrow: it
accepts the ways a number or a list is normally written and nothing cleverer, so a
wrong answer cannot be argued into a right one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plannotation.bench.questions import Answer

#: Relative tolerance for a numeric answer (design brief section 13).
NUMERIC_TOLERANCE = 0.01

#: ``1:50``, ``1 : 50``, ``M 1:50``: the scale's denominator is the number wanted.
_SCALE = re.compile(r"1\s*:\s*(\d[\d\s.,']*)")

#: A number as people write it, with optional thousands separators, a unit after it.
_NUMBER = re.compile(r"[-+]?\d[\d\s.,']*(?:\s*(mm|cm|m)\b)?", re.IGNORECASE)

#: How many of each unit make one of the key's unit.
_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}

#: What separates list items: commas, semicolons, slashes, whitespace, "and".
_LIST_SEPARATOR = re.compile(r"\s*(?:[,;/]|\band\b|\s)\s*", re.IGNORECASE)

#: Wrapping a model may put round a bare value.
_WRAPPING = " \t\r\n\"'`.()[]{}"


def score(expected: Answer, given: str | None, *, unit: str | None = None) -> bool:
    """Say whether an answer matches the key.

    Args:
        expected: The key.
        given: The model's answer, or None when it gave none (a refusal, or output
            cut off before the answer).
        unit: The unit a numeric key is in; an answer that names another length unit
            is converted before it is compared.

    Returns:
        True when the answer is right.
    """
    if given is None:
        return False
    if isinstance(expected, tuple):
        return _as_set(given) == {item.casefold() for item in expected}
    if isinstance(expected, int | float):
        value = parse_number(given, unit=unit)
        if value is None:
            return False
        if expected == 0:
            return value == 0
        return abs(value - expected) <= NUMERIC_TOLERANCE * abs(expected)
    return _normalise(given) == _normalise(expected)


def parse_number(text: str, *, unit: str | None = None) -> float | None:
    """Read the number an answer gives.

    A scale such as ``1:50`` gives its denominator. Otherwise the first number is
    taken, with ``8,000``, ``8 000`` and ``8'000`` read as eight thousand, a lone
    comma before other than three digits read as a decimal comma, and a unit after
    it converted into ``unit`` when both are lengths.

    Args:
        text: The answer.
        unit: The unit the caller wants the number in, if any.

    Returns:
        The number, or None if the answer has none.
    """
    scale = _SCALE.search(text)
    if scale is not None:
        return _to_float(scale.group(1))
    match = _NUMBER.search(text)
    if match is None:
        return None
    written_unit = match.group(1)
    digits = match.group(0)[: match.start(1) - match.start(0)] if written_unit else match.group(0)
    value = _to_float(digits)
    if value is None or written_unit is None or unit not in _UNIT_TO_MM:
        return value
    return value * _UNIT_TO_MM[written_unit.lower()] / _UNIT_TO_MM[str(unit)]


def _to_float(digits: str) -> float | None:
    """Turn a written number into a float, resolving its separators.

    Args:
        digits: The number as written, possibly with separators and trailing space.

    Returns:
        The value, or None if what is left is not a number.
    """
    text = re.sub(r"[\s']", "", digits).rstrip(".,")
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        grouped = re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+", text)
        text = text.replace(",", "") if grouped else text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _as_set(text: str) -> set[str]:
    """Split a list answer into its items.

    Args:
        text: The answer, such as ``"1, 2, A and B"``.

    Returns:
        The items, case-folded, with empty pieces dropped.
    """
    items = _LIST_SEPARATOR.split(text.strip(_WRAPPING))
    return {item.strip(_WRAPPING).casefold() for item in items if item.strip(_WRAPPING)}


def _normalise(text: str) -> str:
    """Reduce a word answer to what is compared: case-folded, unwrapped, one space.

    Args:
        text: The answer or the key.

    Returns:
        The comparable form.
    """
    return " ".join(text.strip(_WRAPPING).split()).casefold()
