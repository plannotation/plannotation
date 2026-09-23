# SPDX-License-Identifier: Apache-2.0
r"""The textual patterns inference recognises, in one place.

A German construction drawing and an English one say the same things in different
words, and the same drawing office writes them in more than one way. These patterns
are the vocabulary; everything else in :mod:`plannotation.infer` is geometry.

Each pattern is anchored with ``\\A`` and ``\\Z`` rather than ``^`` and ``$``, because
``$`` also matches before a trailing newline, and a pattern that accepts
``"Pos. 3\\n"`` is a pattern whose idea of a whole word differs from the reader's.
"""

from __future__ import annotations

import re
from typing import Final

#: A sheet number as drawing offices print it: letters, a dash, digits, e.g. ARC-101.
#: A single-letter discipline prefix -- A-101, S-12 -- is standard in much of the
#: world, so one letter is allowed; two digits are still required, which is what
#: keeps a grid label such as A-1 from reading as a sheet number.
SHEET_ID: Final = re.compile(r"\A[A-Z]{1,4}-\d{2,4}[A-Z]?\Z")

#: A drawing scale: "1:50", "M 1:50", "M1:50", "Maßstab 1:50", "Scale 1:50".
SCALE: Final = re.compile(r"\A(?:M(?:aßstab)?|Scale)?\s*1\s*:\s*(\d{1,5})\Z", re.IGNORECASE)

#: A revision index: "Index A", "IndexA", "Rev B", "Rev. C".
REVISION: Final = re.compile(r"\A(?:Index|Rev\.?)\s*([A-Z0-9]{1,3})\Z", re.IGNORECASE)

#: A grid axis label: one letter, or one or two digits.
GRID_AXIS: Final = re.compile(r"\A(?:[A-Z]|\d{1,2})\Z")

#: A dimension value: "8000", "2,50", "2.50", "2 500".
DIMENSION: Final = re.compile(r"\A\d{1,6}(?:[.,]\d{1,3})?\Z")

#: A level: an elevation in metres with its sign, as a section prints it: "±0,00",
#: "+3,00", "-0,25". The sign is what tells it from a dimension.
LEVEL: Final = re.compile(r"\A([±+\-])(\d{1,3}[.,]\d{2,3})\Z")

#: Structural and architectural marks, with the IFC class each family implies. The
#: class is a guess from the mark's family and is recorded with a confidence to match.
TAG_FAMILIES: Final[tuple[tuple[re.Pattern[str], str, float], ...]] = (
    (re.compile(r"\APos\.?\s*\d+\Z", re.IGNORECASE), "IfcBuildingElement", 0.6),
    (re.compile(r"\ASt\.?\s*\d+\Z", re.IGNORECASE), "IfcColumn", 0.7),
    (re.compile(r"\AUZ[-\s]?\d+\Z", re.IGNORECASE), "IfcBeam", 0.7),
    (re.compile(r"\AU\d+\Z"), "IfcBeam", 0.5),
    (re.compile(r"\AW[-\s]?\d+\Z"), "IfcWindow", 0.6),
    (re.compile(r"\AT[-\s]?\d+\Z"), "IfcDoor", 0.6),
    (re.compile(r"\AD[-\s]?\d+\Z"), "IfcDoor", 0.5),
    (re.compile(r"\AS\d+\Z"), "IfcSlab", 0.5),
)

#: Words that say what kind of drawing a sheet is, with the drawingType each implies.
DRAWING_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("positionsplan", "positionsplan"),
    ("schalplan", "schalplan"),
    ("bewehrungsplan", "bewehrungsplan"),
    ("lageplan", "lageplan"),
    ("grundriss", "plan"),
    ("floor plan", "plan"),
    ("plan", "plan"),
    ("schnitt", "section"),
    ("section", "section"),
    ("ansicht", "elevation"),
    ("elevation", "elevation"),
    ("detail", "detail"),
)

#: Drawing types that belong to structural engineering rather than architecture.
STRUCTURAL_TYPES: Final = frozenset({"positionsplan", "schalplan", "bewehrungsplan"})

#: Words in a title block's labels that introduce the value beside them.
TITLE_BLOCK_KEYS: Final = re.compile(
    r"(?:Projekt|Project|Plan-Nr|Sheet|Blatt|Maßstab|Scale|Index|Rev|Datum|Date|"
    r"gez\.|gezeichnet|Drawn|gepr\.|geprüft|Checked)",
    re.IGNORECASE,
)


def tag_family(text: str) -> tuple[str, float] | None:
    """Return the IFC class a mark's family implies, and how sure that is.

    Args:
        text: The mark as printed.

    Returns:
        ``(ifc_class, confidence)``, or None when the text is not a mark.
    """
    for pattern, ifc_class, confidence in TAG_FAMILIES:
        if pattern.match(text.strip()):
            return ifc_class, confidence
    return None


def parse_level(text: str) -> float | None:
    """Read a printed level as an elevation in metres.

    Args:
        text: The printed level, such as ``+3,00``.

    Returns:
        The elevation, or None when the text is not a level.

    Examples:
        >>> parse_level("-0,25")
        -0.25
    """
    match = LEVEL.match(text.strip())
    if match is None:
        return None
    value = float(match.group(2).replace(",", "."))
    return -value if match.group(1) == "-" else value


def parse_dimension(text: str) -> float | None:
    """Read a printed dimension as a number.

    A comma is a decimal separator here, as it is on a German drawing, so "2,50" is two
    and a half. A space is a thousands separator, so "2 500" is two and a half thousand.

    Args:
        text: The printed value.

    Returns:
        The number, or None when the text is not a dimension.
    """
    cleaned = text.strip()
    if not DIMENSION.match(cleaned.replace(" ", "")):
        return None
    return float(cleaned.replace(" ", "").replace(",", "."))
