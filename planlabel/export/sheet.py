# SPDX-License-Identifier: Apache-2.0
"""Compose a drawing sheet around a rendered view.

A sheet is a page of paper with a frame, a title block and one or more viewports on it.
This module writes that SVG, and it writes it as text rather than through a DOM, for
one reason: every number it emits is also a number the label records, and keeping the
two in one place is what stops them disagreeing. A sheet built here returns both the
SVG and the paper geometry of everything it drew.

The SVG is written in paper millimetres with a ``viewBox`` of one user unit per
millimetre, which is what the serializer already produces, so nothing is scaled on the
way in. SVG is y-down and paper is y-up, so every y written here is ``height - y``;
that flip happens in exactly one function.

Nothing here is decorative. A frame, a title block, grid bubbles, dimensions, tags and
a callout are the parts of a sheet that a label has something to say about, and a
sample that omitted them would exercise nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from planlabel.units import COORD_DECIMALS

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
        opens carries the same digits the label does.
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

    def line(self, x0: float, y0: float, x1: float, y1: float, *, width: float = 0.25) -> None:
        """Draw a straight line in paper coordinates.

        Args:
            x0: Start x.
            y0: Start y.
            x1: End x.
            y1: End y.
            width: Stroke width in millimetres.
        """
        self.parts.append(
            f'<line x1="{_n(x0)}" y1="{_n(self.y(y0))}" x2="{_n(x1)}" y2="{_n(self.y(y1))}" '
            f'stroke="#000" stroke-width="{_n(width)}"/>'
        )

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
        self, x: float, y: float, value: str, *, size: float = 3.5, anchor: str = "start"
    ) -> None:
        """Draw text at a paper position.

        Args:
            x: Paper x.
            y: Paper y of the baseline.
            value: The text, which is written as it will print.
            size: Font size in millimetres.
            anchor: SVG ``text-anchor``.
        """
        escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.parts.append(
            f'<text x="{_n(x)}" y="{_n(self.y(y))}" font-family="Helvetica" '
            f'font-size="{_n(size)}" text-anchor="{anchor}" fill="#000">{escaped}</text>'
        )

    def circle(self, x: float, y: float, radius: float, *, width: float = 0.25) -> None:
        """Draw a circle at a paper position.

        Args:
            x: Paper x of the centre.
            y: Paper y of the centre.
            radius: Radius in millimetres.
            width: Stroke width in millimetres.
        """
        self.parts.append(
            f'<circle cx="{_n(x)}" cy="{_n(self.y(y))}" r="{_n(radius)}" fill="#fff" '
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


def frame_box(width_mm: float, height_mm: float) -> tuple[float, float, float, float]:
    """Return the drawing frame's paper bounding box.

    Args:
        width_mm: The page width.
        height_mm: The page height.

    Returns:
        ``(x0, y0, x1, y1)`` in paper millimetres.
    """
    return (MARGIN_MM, MARGIN_MM, width_mm - MARGIN_MM, height_mm - MARGIN_MM)


def title_block_box(width_mm: float, height_mm: float) -> tuple[float, float, float, float]:
    """Return the title block's paper bounding box, at the frame's bottom-right.

    Args:
        width_mm: The page width.
        height_mm: The page height.

    Returns:
        ``(x0, y0, x1, y1)`` in paper millimetres.
    """
    _, _, right, _ = frame_box(width_mm, height_mm)
    block_width, block_height = TITLE_BLOCK_MM
    return (right - block_width, MARGIN_MM, right, MARGIN_MM + block_height)
