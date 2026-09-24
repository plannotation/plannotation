# SPDX-License-Identifier: Apache-2.0
"""Compose a drawing sheet around a rendered view.

A sheet is a page of paper with a frame, a title block and one or more viewports on it.
This module writes that SVG, and it writes it as text rather than through a DOM, for
one reason: every number it emits is also a number the plannotation records, and keeping
the two in one place is what stops them disagreeing. A sheet built here returns both the
SVG and the paper geometry of everything it drew.

The SVG is written in paper millimetres with a ``viewBox`` of one user unit per
millimetre, which is what the serializer already produces, so nothing is scaled on the
way in. SVG is y-down and paper is y-up, so every y written here is ``height - y``;
that flip happens in exactly one function.

Nothing here is decorative. A frame, a title block, grid bubbles, dimensions, tags and
a callout are the parts of a sheet that a plannotation has something to say about, and
a sample that omitted them would exercise nothing.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from plannotation.units import COORD_DECIMALS

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The A-series sizes a construction drawing is issued at, in millimetres.
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}

#: Distance from the page edge to the drawing frame.
MARGIN_MM = 10.0

#: The title block's size, bottom-right of the frame.
TITLE_BLOCK_MM = (180.0, 60.0)


def _n(value: float) -> str:
    """Format a number for SVG at the precision the format serialises at.

    Args:
        value: The number.

    Returns:
        Its text, without a trailing ``.0`` on a whole number, so that the SVG a reader
        opens carries the same digits the plannotation does.
    """
    rounded = round(float(value), COORD_DECIMALS)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


@dataclass
class Sheet:
    """A sheet being composed.

    Attributes:
        width_mm: The page width.
        height_mm: The page height.
        parts: The SVG fragments written so far, in document order.
    """

    width_mm: float
    height_mm: float
    parts: list[str] = field(default_factory=list)

    def y(self, paper_y: float) -> float:
        """Flip a paper y into SVG.

        Args:
            paper_y: Paper y in millimetres, measured up from the bottom.

        Returns:
            The SVG y, measured down from the top.
        """
        return self.height_mm - paper_y

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        width: float = 0.25,
        dash: Sequence[float] | None = None,
    ) -> None:
        """Draw a straight line in paper coordinates.

        Args:
            x0: Start x.
            y0: Start y.
            x1: End x.
            y1: End y.
            width: Stroke width in millimetres.
            dash: Dash and gap lengths in millimetres, for a chain line; None for a
                continuous one.
        """
        pattern = f' stroke-dasharray="{" ".join(_n(v) for v in dash)}"' if dash else ""
        self.parts.append(
            f'<line x1="{_n(x0)}" y1="{_n(self.y(y0))}" x2="{_n(x1)}" y2="{_n(self.y(y1))}" '
            f'stroke="#000" stroke-width="{_n(width)}"{pattern}/>'
        )

    def polyline(
        self,
        points: Sequence[Sequence[float]],
        *,
        width: float = 0.25,
        fill: str = "none",
        closed: bool = False,
    ) -> None:
        """Draw a polyline or a polygon in paper coordinates.

        Args:
            points: The vertices in paper millimetres.
            width: Stroke width in millimetres; 0 for no stroke.
            fill: SVG fill, for a closed shape.
            closed: Whether the last vertex joins the first.
        """
        path = " ".join(
            f"{'M' if index == 0 else 'L'}{_n(x)},{_n(self.y(y))}"
            for index, (x, y) in enumerate(points)
        )
        stroke = f'stroke="#000" stroke-width="{_n(width)}"' if width else 'stroke="none"'
        self.parts.append(f'<path d="{path}{" Z" if closed else ""}" fill="{fill}" {stroke}/>')

    def rect(self, box: Sequence[float], *, width: float = 0.35, fill: str = "none") -> None:
        """Draw a rectangle from a paper bounding box.

        Args:
            box: ``(x0, y0, x1, y1)`` in paper millimetres.
            width: Stroke width in millimetres.
            fill: SVG fill.
        """
        x0, y0, x1, y1 = box
        self.parts.append(
            f'<rect x="{_n(x0)}" y="{_n(self.y(y1))}" width="{_n(x1 - x0)}" '
            f'height="{_n(y1 - y0)}" fill="{fill}" stroke="#000" stroke-width="{_n(width)}"/>'
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        size: float = 3.5,
        anchor: str = "start",
        bold: bool = False,
    ) -> None:
        """Draw text at a paper position.

        Args:
            x: Paper x.
            y: Paper y of the baseline.
            value: The text, which is written as it will print.
            size: Font size in millimetres.
            anchor: SVG ``text-anchor``.
            bold: Whether it prints bold.
        """
        escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        weight = ' font-weight="bold"' if bold else ""
        self.parts.append(
            f'<text x="{_n(x)}" y="{_n(self.y(y))}" font-family="Helvetica" '
            f'font-size="{_n(size)}"{weight} text-anchor="{anchor}" fill="#000">{escaped}</text>'
        )

    def circle(
        self, x: float, y: float, radius: float, *, width: float = 0.25, fill: str = "#fff"
    ) -> None:
        """Draw a circle at a paper position.

        Args:
            x: Paper x of the centre.
            y: Paper y of the centre.
            radius: Radius in millimetres.
            width: Stroke width in millimetres.
            fill: SVG fill.
        """
        self.parts.append(
            f'<circle cx="{_n(x)}" cy="{_n(self.y(y))}" r="{_n(radius)}" fill="{fill}" '
            f'stroke="#000" stroke-width="{_n(width)}"/>'
        )

    def group(self, inner: str, *, transform: str | None = None, attributes: str = "") -> None:
        """Add a group, optionally transformed.

        Args:
            inner: The group's contents, already SVG.
            transform: An SVG ``transform``, or None.
            attributes: Further attributes, already formatted.
        """
        parts = ["<g"]
        if transform:
            parts.append(f' transform="{transform}"')
        if attributes:
            parts.append(f" {attributes}")
        parts.append(f">{inner}</g>")
        self.parts.append("".join(parts))

    def render(self) -> str:
        """Return the composed sheet.

        Returns:
            The SVG document, with a viewBox of one user unit per millimetre so that a
            reader measuring the drawing measures millimetres.
        """
        body = "\n  ".join(self.parts)
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'xmlns:ifc="http://www.ifcopenshell.org/ns" '
            f'width="{_n(self.width_mm)}mm" height="{_n(self.height_mm)}mm" '
            f'viewBox="0 0 {_n(self.width_mm)} {_n(self.height_mm)}">\n'
            f"  {body}\n</svg>\n"
        )


#: Helvetica's advance widths in thousandths of an em, from its public font metrics,
#: for the characters a drawing prints. A letter with a diacritic takes its base
#: letter's width; anything else is taken as wide as an ``n``.
_HELVETICA_WIDTHS = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "
    "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "
    "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 "
    "556 556 333 500 278 556 500 722 500 500 500 334 260 334 584 "
)
_HELVETICA = dict(
    zip(
        " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`"
        "abcdefghijklmnopqrstuvwxyz{|}~",
        (int(width) for width in _HELVETICA_WIDTHS.split()),
        strict=True,
    )
) | {"²": 333, "±": 584, "©": 737, "°": 400, "\u2013": 556, "\u2014": 1000, "\u2019": 222}

#: How much wider bold Helvetica sets than regular, on average.
_BOLD_WIDENING = 1.07


def text_width(value: str, size: float, *, bold: bool = False) -> float:
    """Estimate how wide a line of Helvetica prints, for laying it out.

    Args:
        value: The text.
        size: Its font size in millimetres.
        bold: Whether it prints bold.

    Returns:
        Its width in millimetres.
    """
    total = 0
    for character in value:
        base = unicodedata.normalize("NFD", character)[0]
        total += _HELVETICA.get(character, _HELVETICA.get(base, _HELVETICA["n"]))
    return total / 1000.0 * size * (_BOLD_WIDENING if bold else 1.0)


def frame_box(width_mm: float, height_mm: float) -> tuple[float, float, float, float]:
    """Return the drawing frame's paper bounding box.

    Args:
        width_mm: The page width.
        height_mm: The page height.

    Returns:
        ``(x0, y0, x1, y1)`` in paper millimetres.
    """
    return (MARGIN_MM, MARGIN_MM, width_mm - MARGIN_MM, height_mm - MARGIN_MM)


def title_block_box(
    width_mm: float, height_mm: float, size_mm: tuple[float, float] | None = None
) -> tuple[float, float, float, float]:
    """Return the title block's paper bounding box, at the frame's bottom-right.

    Args:
        width_mm: The page width.
        height_mm: The page height.
        size_mm: The block's width and height, or None for :data:`TITLE_BLOCK_MM`.

    Returns:
        ``(x0, y0, x1, y1)`` in paper millimetres.
    """
    _, _, right, _ = frame_box(width_mm, height_mm)
    block_width, block_height = size_mm or TITLE_BLOCK_MM
    return (right - block_width, MARGIN_MM, right, MARGIN_MM + block_height)
