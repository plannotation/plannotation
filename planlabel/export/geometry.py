# SPDX-License-Identifier: Apache-2.0
"""Turn the SVG the serializer drew into the paper geometry a label records.

A product group holds one or more ``d`` attributes in SVG user units, y down. A label
records a ``paperBBox`` and optional ``paperOutlines`` in paper millimetres, y up, with
the origin at the bottom-left of the sheet (SPEC 3.1, 3.3). The conversion is the y-flip
and, when the view has been placed on a sheet, the wrapper transform that placed it.

Paths are parsed with ``svgelements`` rather than by reading the ``d`` string directly.
The serializer writes only moves and lines today, and a regular expression over ``M``
and ``L`` would work today, but a path is a small language and a reader that handles
the subset it has seen is a reader that breaks on the first curve. ``svgelements``
already knows the whole language, and flattening whatever it returns to points is the
same amount of code as handling two commands by hand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from svgelements import Path

from planlabel.export.paper import svg_to_paper
from planlabel.units import COORD_DECIMALS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: How finely a curve is flattened, in paper millimetres. Well below the 0.5 mm
#: geometric tolerance of SPEC 3.1, so flattening can never be what puts a point
#: outside its own bounding box.
CURVE_STEP_MM = 0.1


def path_points(
    d_attribute: str,
    *,
    page_height_mm: float,
    offset: tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
) -> list[tuple[float, float]]:
    """Flatten one SVG path into paper-millimetre points.

    Args:
        d_attribute: The path's ``d``.
        page_height_mm: The sheet's height, for the y-flip.
        offset: The translation of the wrapper that placed this view on the sheet.
        scale: That wrapper's uniform scale.

    Returns:
        The path's points in paper millimetres, in order, rounded to the precision the
        format serialises at so that what is recorded is what was measured.
    """
    points: list[tuple[float, float]] = []
    for segment in Path(d_attribute).segments():
        for raw in segment_points(segment):
            x = raw[0] * scale + offset[0]
            y = raw[1] * scale + offset[1]
            paper = svg_to_paper(x, y, page_height_mm)
            rounded = (round(paper[0], COORD_DECIMALS), round(paper[1], COORD_DECIMALS))
            if not points or points[-1] != rounded:
                points.append(rounded)
    return points


def segment_points(segment: object) -> list[tuple[float, float]]:
    """Return the points of one path segment, flattening a curve if it is one.

    Args:
        segment: A ``svgelements`` segment.

    Returns:
        Its points in SVG user units.
    """
    start = getattr(segment, "start", None)
    end = getattr(segment, "end", None)
    if start is None and end is None:
        return []
    if type(segment).__name__ in {"Line", "Close", "Move"}:
        return [(float(p.x), float(p.y)) for p in (start, end) if p is not None]
    length = float(getattr(segment, "length", lambda **_: 0.0)(error=1e-3) or 0.0)
    steps = max(2, int(length / CURVE_STEP_MM) + 1)
    return [
        (float(point.x), float(point.y))
        for point in (segment.point(index / steps) for index in range(steps + 1))  # type: ignore[attr-defined]
    ]


def bounding_box(points: Iterable[Sequence[float]]) -> tuple[float, float, float, float]:
    """Return the axis-aligned bounds of some points.

    Args:
        points: Points in paper millimetres.

    Returns:
        ``(x0, y0, x1, y1)`` ordered with the lower-left corner first, as SPEC 3.4
        requires.

    Raises:
        ValueError: If there are no points, which has no bounding box and must not be
            silently reported as one at the origin.
    """
    collected = [(float(point[0]), float(point[1])) for point in points]
    if not collected:
        msg = "cannot take the bounding box of no points"
        raise ValueError(msg)
    xs = [point[0] for point in collected]
    ys = [point[1] for point in collected]
    return (
        round(min(xs), COORD_DECIMALS),
        round(min(ys), COORD_DECIMALS),
        round(max(xs), COORD_DECIMALS),
        round(max(ys), COORD_DECIMALS),
    )


def union_box(
    boxes: Iterable[Sequence[float]],
) -> tuple[float, float, float, float]:
    """Return the box containing every box given.

    Args:
        boxes: Bounding boxes in paper millimetres.

    Returns:
        The smallest box containing them all.

    Raises:
        ValueError: If no boxes were given.
    """
    collected = list(boxes)
    if not collected:
        msg = "cannot take the union of no boxes"
        raise ValueError(msg)
    return bounding_box(
        [(box[0], box[1]) for box in collected] + [(box[2], box[3]) for box in collected]
    )
