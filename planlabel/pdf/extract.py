# SPDX-License-Identifier: Apache-2.0
"""Extract words, lines and circles from a PDF page, in paper millimetres.

This is what the inference pass reads. It returns everything in the coordinate system
a label uses -- millimetres, origin bottom-left, y up (SPEC 3.1) -- so that nothing
downstream has to know pdfplumber measures from the top of the page in points.

pdfplumber (MIT) does the reading. It is chosen over PyMuPDF because PyMuPDF is AGPL,
which the project's licence policy forbids outright; pdfplumber reads text with its
positions and the vector drawing operators, which is everything inference needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pdfplumber

from planlabel.units import COORD_DECIMALS, MM_PER_PT

if TYPE_CHECKING:
    from pathlib import Path

#: How close to a circle a curve's bounding box must be, as a ratio of its width to
#: its height, to be read as one. Grid bubbles are circles; a drawn arc is not.
CIRCLE_ASPECT_TOLERANCE = 0.15


@dataclass(frozen=True)
class Word:
    """One word on the page.

    Attributes:
        text: The word as printed.
        bbox: Its box in paper millimetres, ``(x0, y0, x1, y1)``.
    """

    text: str
    bbox: tuple[float, float, float, float]

    @property
    def centre(self) -> tuple[float, float]:
        """Return the word's centre in paper millimetres."""
        return ((self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0)


@dataclass(frozen=True)
class Segment:
    """One straight line segment.

    Attributes:
        start: One end, in paper millimetres.
        end: The other end.
    """

    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def length(self) -> float:
        """Return the segment's length in paper millimetres."""
        return float(
            ((self.end[0] - self.start[0]) ** 2 + (self.end[1] - self.start[1]) ** 2) ** 0.5
        )

    @property
    def horizontal(self) -> bool:
        """Report whether the segment runs across the page."""
        return abs(self.end[1] - self.start[1]) < abs(self.end[0] - self.start[0])


@dataclass(frozen=True)
class Circle:
    """A closed curve round enough to be a grid bubble or a callout.

    Attributes:
        centre: Its centre in paper millimetres.
        radius: Its radius in millimetres.
    """

    centre: tuple[float, float]
    radius: float


@dataclass(frozen=True)
class PageContent:
    """Everything inference reads from one page.

    Attributes:
        width_mm: The page width.
        height_mm: The page height.
        words: Every word, in reading order.
        segments: Every straight line segment.
        circles: Every curve round enough to be a circle.
    """

    width_mm: float
    height_mm: float
    words: tuple[Word, ...]
    segments: tuple[Segment, ...]
    circles: tuple[Circle, ...]


def _mm(value: float) -> float:
    """Convert points to millimetres, rounded to the serialised precision.

    Args:
        value: A length in PDF points.

    Returns:
        The same length in millimetres.
    """
    return round(float(value) * MM_PER_PT, COORD_DECIMALS)


def extract_page(path: Path, page_index: int) -> PageContent:
    """Read one page into paper coordinates.

    Args:
        path: The PDF.
        page_index: The zero-based page index.

    Returns:
        The page's words, segments and circles.

    Raises:
        IndexError: If the document has no such page.
    """
    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[page_index]
        height_pt = float(page.height)

        def flip(y_top: float) -> float:
            """Convert a distance from the top in points to paper y in millimetres."""
            return _mm(height_pt - y_top)

        words = tuple(
            Word(
                text=str(word["text"]),
                bbox=(
                    _mm(word["x0"]),
                    flip(word["bottom"]),
                    _mm(word["x1"]),
                    flip(word["top"]),
                ),
            )
            for word in page.extract_words(keep_blank_chars=False, use_text_flow=False)
        )
        segments = tuple(
            Segment(
                start=(_mm(line["x0"]), flip(line["top"])),
                end=(_mm(line["x1"]), flip(line["bottom"])),
            )
            for line in page.lines
        )
        circles = tuple(
            Circle(
                centre=(
                    _mm((curve["x0"] + curve["x1"]) / 2.0),
                    flip((curve["top"] + curve["bottom"]) / 2.0),
                ),
                radius=_mm((curve["x1"] - curve["x0"]) / 2.0),
            )
            for curve in page.curves
            if _is_round(curve)
        )
        return PageContent(
            width_mm=_mm(page.width),
            height_mm=_mm(height_pt),
            words=words,
            segments=segments,
            circles=circles,
        )


def page_text(path: Path, page_index: int) -> str:
    """Return a page's text as a PDF reader would copy it out, in reading order.

    This is the text a model gets when it is handed a PDF with no label: pdfplumber's
    layout-free extraction, top to bottom and left to right.

    Args:
        path: The PDF.
        page_index: The zero-based page.

    Returns:
        The page's text, lines separated by newlines.

    Raises:
        IndexError: If the document has no such page.
    """
    with pdfplumber.open(str(path)) as pdf:
        if not 0 <= page_index < len(pdf.pages):
            msg = f"{path} has {len(pdf.pages)} page(s); there is no page index {page_index}"
            raise IndexError(msg)
        return str(pdf.pages[page_index].extract_text())


def page_count(path: Path) -> int:
    """Return how many pages a PDF has.

    Args:
        path: The PDF.

    Returns:
        Its page count.
    """
    with pdfplumber.open(str(path)) as pdf:
        return len(pdf.pages)


def _is_round(curve: dict[str, float]) -> bool:
    """Report whether a curve's bounding box is square enough to be a circle.

    Args:
        curve: A pdfplumber curve object.

    Returns:
        True when width and height agree within the tolerance.
    """
    width = float(curve["x1"]) - float(curve["x0"])
    height = float(curve["bottom"]) - float(curve["top"])
    if width <= 0 or height <= 0:
        return False
    return abs(width - height) / max(width, height) <= CIRCLE_ASPECT_TOLERANCE
