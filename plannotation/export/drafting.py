# SPDX-License-Identifier: Apache-2.0
"""An architect's conventions on a composed sheet.

The serializer's own drawing carries its look in a ``<style>`` block keyed on class
names, and a composed sheet keeps only the view group, so every path in it falls back
to SVG's default: filled black. A plan in which every window, door and room is a black
blob is not a plan. This module restores what a reader expects of a drawing at 1:100,
as presentation attributes on each product's group, where they survive composition:

* cut load-bearing walls and columns -- and slabs, beams and roofs -- solid black;
  cut walls that bear nothing grey; everything else cut, windows and doors among
  them, in outline; spaces not drawn at all, only labelled; what is seen beyond the cut
  in fine lines. Line weights are graded from cut to seen to annotation.
* chain lines for grids, section marks with the sheet they point at, a view title with
  its scale, a north arrow turned to the model's true north, and a scale bar.
* room labels -- number, name and area -- placed inside each room, clear of every line
  drawn in it.

Everything here is drawing; what it draws is decided by the model, in
:mod:`plannotation.export.ifc_svg_pdf`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np

from plannotation.export.geometry import bounding_box
from plannotation.export.sheet import text_width

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from numpy.typing import NDArray

    from plannotation.export.sheet import Sheet

#: A paper bounding box, ``(x0, y0, x1, y1)`` in millimetres.
Box = tuple[float, float, float, float]

#: A point on the paper, in millimetres.
Point = tuple[float, float]

#: Classes that are structure wherever they are cut, whatever their property sets say.
STRUCTURAL = ("IfcColumn", "IfcBeam", "IfcSlab", "IfcRoof", "IfcFooting", "IfcPile")

#: Classes drawn grey when cut and not stated to bear load.
PARTITIONS = ("IfcWall",)

#: The fills and strokes of cut elements by kind, and of what is seen beyond the cut.
#: The grey is light enough for a black line on it to read and dark enough to tell a
#: partition from a void.
STYLES: dict[str, str] = {
    "solid": 'fill="#000" fill-rule="evenodd" stroke="#000" stroke-width="0.25"',
    "partition": 'fill="#9a9a9a" fill-rule="evenodd" stroke="#000" stroke-width="0.18"',
    "outline": 'fill="none" stroke="#000" stroke-width="0.13"',
    "hidden": 'fill="none" stroke="none"',
    "seen": 'fill="none" stroke="#000" stroke-width="0.1"',
}

#: A grid's chain line: long dash, gap, dot, gap, in millimetres.
GRID_DASH = (8.0, 1.5, 0.6, 1.5)

#: Line weights of annotation, in millimetres.
FINE_MM, MEDIUM_MM, HEAVY_MM = 0.13, 0.25, 0.5


@dataclass(frozen=True)
class RoomLabels:
    """How rooms are labelled: number, name and area, inside each room.

    Attributes:
        area_property: The property holding a room's area, as ``PsetName.Property``;
            None to print no area. A model's own area is printed as the model states it,
            and an area of zero is left off rather than printed.
        area_unit: The unit printed after the area.
    """

    area_property: str | None = None
    area_unit: str = "m²"


@dataclass(frozen=True)
class SectionMark:
    """A section's cutting line on a plan: a mark at each end, pointing the way it looks.

    Attributes:
        label: The section's name, such as ``A``.
        target_sheet: The sheet the section is drawn on.
        position: The line's plane coordinate, in metres: plane x for a line up the
            page, plane y for one across it.
        vertical: True for a line up the page.
        looking: -1 when the section looks towards decreasing plane coordinates, +1
            towards increasing ones.
    """

    label: str
    target_sheet: str
    position: float
    vertical: bool = True
    looking: int = -1


@dataclass(frozen=True)
class ViewTitle:
    """The title under a view: its name and scale, and where it is marked.

    Attributes:
        text: The view's name as it prints.
        label: The name in the callout bubble, such as ``A``; None for no bubble.
        target_sheet: The sheet on which this view is marked, printed in the bubble
            and linked as the callout's target.
    """

    text: str
    label: str | None = None
    target_sheet: str | None = None


def style_of(ifc_class: str, *, load_bearing: bool | None, is_a: Callable[[str, str], bool]) -> str:
    """Return how a cut element is drawn.

    Args:
        ifc_class: Its IFC class.
        load_bearing: Its common property set's ``LoadBearing``, or None.
        is_a: Says whether a class is a given class or a subtype of it.

    Returns:
        A key of :data:`STYLES`.
    """
    if is_a(ifc_class, "IfcSpace"):
        return "hidden"
    if load_bearing is True or any(is_a(ifc_class, name) for name in STRUCTURAL):
        return "solid"
    if any(is_a(ifc_class, name) for name in PARTITIONS):
        return "partition"
    return "outline"


def present(group: str, styles: dict[str, str]) -> str:
    """Give each product group in a view its presentation attributes.

    Args:
        group: The serializer's view group.
        styles: A key of :data:`STYLES` for each GlobalId; a product not named is
            outlined.

    Returns:
        The group, with the attributes on each product's ``<g>`` and on the group of
        lines seen beyond the cut.
    """

    def product(match: re.Match[str]) -> str:
        guid = re.search(r'ifc:guid="([^"]+)"', match.group(1))
        key = styles.get(guid.group(1), "outline") if guid else "outline"
        return f"<g{match.group(1).rstrip()} {STYLES[key]}>"

    group = re.sub(r"<g\b([^>]*\bifc:guid=\"[^\"]+\"[^>]*)>", product, group)
    return group.replace('<g class="projection">', f'<g class="projection" {STYLES["seen"]}>')


# ---------------------------------------------------------------------------
# Geometry on the paper
# ---------------------------------------------------------------------------
def segments_of(polylines: Sequence[Sequence[Point]]) -> NDArray[np.float64]:
    """Return the straight segments of some polylines as an ``(n, 4)`` array.

    Args:
        polylines: Polylines in paper millimetres.

    Returns:
        One row ``x0, y0, x1, y1`` per segment.
    """
    rows = [(a[0], a[1], b[0], b[1]) for line in polylines for a, b in pairwise(line) if a != b]
    return np.array(rows, dtype=np.float64).reshape(-1, 4)


def hits(segments: NDArray[np.float64], box: Box) -> bool:
    """Say whether any segment touches a box.

    Liang and Barsky's clip, over every segment at once.

    Args:
        segments: An ``(n, 4)`` array of segments.
        box: The box.

    Returns:
        True when at least one segment has a point in the box.
    """
    if not len(segments):
        return False
    x0, y0, x1, y1 = segments.T
    near = ~(
        (np.maximum(x0, x1) < box[0])
        | (np.minimum(x0, x1) > box[2])
        | (np.maximum(y0, y1) < box[1])
        | (np.minimum(y0, y1) > box[3])
    )
    if not near.any():
        return False
    x0, y0, x1, y1 = segments[near].T
    dx, dy = x1 - x0, y1 - y0
    low = np.zeros_like(x0)
    high = np.ones_like(x0)
    alive = np.ones_like(x0, dtype=bool)
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        parallel = p == 0
        alive &= ~(parallel & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
        low = np.where(~parallel & (p < 0), np.maximum(low, ratio), low)
        high = np.where(~parallel & (p > 0), np.minimum(high, ratio), high)
    return bool((alive & (low <= high)).any())


def inside(point: Point, polygon: Sequence[Point]) -> bool:
    """Say whether a point lies inside a polygon, by the even-odd rule.

    Args:
        point: The point.
        polygon: The polygon's vertices.

    Returns:
        True inside.
    """
    x, y = point
    result = False
    for (x0, y0), (x1, y1) in zip(polygon, [*polygon[1:], polygon[0]], strict=True):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            result = not result
    return result


def centroid(polygon: Sequence[Point]) -> Point:
    """Return a polygon's area centroid, or its vertices' mean if it has no area.

    Args:
        polygon: The polygon's vertices.

    Returns:
        The centroid.
    """
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(polygon, [*polygon[1:], polygon[0]], strict=True):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-9:  # noqa: PLR2004 - no area to speak of
        return (
            sum(p[0] for p in polygon) / len(polygon),
            sum(p[1] for p in polygon) / len(polygon),
        )
    return (cx / (3.0 * area), cy / (3.0 * area))


def overlaps(first: Sequence[float], second: Sequence[float]) -> bool:
    """Say whether two boxes share any area.

    Args:
        first: One box.
        second: The other.

    Returns:
        True when they overlap.
    """
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


# ---------------------------------------------------------------------------
# Room labels
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    """One line of a label.

    Attributes:
        text: What it prints.
        size: Its font size in millimetres.
        bold: Whether it prints bold.
    """

    text: str
    size: float
    bold: bool = False


#: The leading of a label, as a share of each line's size, and its clear margin.
_LEADING, _LABEL_MARGIN_MM = 1.3, 0.6

#: The step between the points a label is tried at, in millimetres.
LABEL_STEP_MM = 0.5


def label_boxes(centre: Point, lines: Sequence[Line]) -> list[Box]:
    """Return the box of each line of a label centred on a point.

    Args:
        centre: The label's centre.
        lines: Its lines, top first.

    Returns:
        One box per line, from its descender to its cap height.
    """
    height = sum(line.size * _LEADING for line in lines)
    top = centre[1] + height / 2.0
    boxes: list[Box] = []
    for line in lines:
        width = text_width(line.text, line.size, bold=line.bold)
        baseline = top - line.size
        boxes.append(
            (
                centre[0] - width / 2.0,
                baseline - 0.22 * line.size,
                centre[0] + width / 2.0,
                baseline + 0.76 * line.size,
            )
        )
        top -= line.size * _LEADING
    return boxes


def place_label(
    polygon: Sequence[Point],
    lines: Sequence[Line],
    segments: NDArray[np.float64],
    placed: Sequence[Box],
    *,
    extra_width: float = 0.0,
) -> Point | None:
    """Find where a label fits inside a room, clear of everything drawn there.

    Points on a :data:`LABEL_STEP_MM` grid over the room are tried nearest the room's
    centroid first. A point is taken when the label's box, with a margin, lies inside
    the room and no line of the drawing and no label already placed touches it.

    Args:
        polygon: The room's outline on the paper.
        lines: The label's lines.
        segments: Every line drawn near the room, the room's own outline among them.
        placed: The boxes of the labels already placed.
        extra_width: Room the label needs beside its text, for a symbol.

    Returns:
        The label's centre, or None where it fits nowhere.
    """
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    target = centroid(polygon)
    if not inside(target, polygon):
        target = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    grid_x = np.arange(min(xs), max(xs), LABEL_STEP_MM)
    grid_y = np.arange(min(ys), max(ys), LABEL_STEP_MM)
    if not len(grid_x) or not len(grid_y):
        return None
    gx, gy = np.meshgrid(grid_x, grid_y)
    order = np.argsort((gx - target[0]) ** 2 + (gy - target[1]) ** 2, axis=None)
    width = max(text_width(line.text, line.size, bold=line.bold) for line in lines) + extra_width
    height = sum(line.size * _LEADING for line in lines)
    near = segments[
        ~(
            (np.maximum(segments[:, 0], segments[:, 2]) < min(xs))
            | (np.minimum(segments[:, 0], segments[:, 2]) > max(xs))
            | (np.maximum(segments[:, 1], segments[:, 3]) < min(ys))
            | (np.minimum(segments[:, 1], segments[:, 3]) > max(ys))
        )
    ]
    for index in order:
        x, y = float(gx.flat[index]), float(gy.flat[index])
        box = (
            x - width / 2.0 - _LABEL_MARGIN_MM,
            y - height / 2.0 - _LABEL_MARGIN_MM,
            x + width / 2.0 + _LABEL_MARGIN_MM,
            y + height / 2.0 + _LABEL_MARGIN_MM,
        )
        corners = ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))
        if not all(inside(corner, polygon) for corner in corners):
            continue
        if any(overlaps(box, other) for other in placed):
            continue
        if hits(near, box):
            continue
        return (round(x, 3), round(y, 3))
    return None


def draw_label(sheet: Sheet, centre: Point, lines: Sequence[Line]) -> list[Box]:
    """Print a label centred on a point.

    Args:
        sheet: The sheet being composed.
        centre: The label's centre.
        lines: Its lines, top first.

    Returns:
        The box of each line, as :func:`label_boxes` gives it.
    """
    boxes = label_boxes(centre, lines)
    for line, box in zip(lines, boxes, strict=True):
        baseline = box[1] + 0.22 * line.size
        sheet.text(centre[0], baseline, line.text, size=line.size, anchor="middle", bold=line.bold)
    return [(round(b[0], 3), round(b[1], 3), round(b[2], 3), round(b[3], 3)) for b in boxes]


def area_text(value: object, unit: str) -> str | None:
    """Print an area as a model states it: ``25,2 m²``.

    Args:
        value: The property's value: a number, or text such as ``25,2``.
        unit: The unit printed after it.

    Returns:
        The printed area, or None for a missing or zero area, which is left off rather
        than printed as a room of no size.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        text = f"{number:.1f}".replace(".", ",")
    else:
        text = str(value).strip()
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            return None
    if number <= 0.0:
        return None
    return f"{text} {unit}"


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------
#: A level mark's triangle: half its width and its height, in millimetres.
LEVEL_TRIANGLE_MM = (1.3, 2.2)


def draw_level_mark(
    sheet: Sheet, start: Point, length: float, text: str, *, size: float = 2.5
) -> tuple[Box, list[Point]]:
    """Draw a level mark: a line, an open triangle standing on it, and the level beside.

    The triangle's point touches the line, which is the height the mark states, and the
    text stands on the line to the triangle's right.

    Args:
        sheet: The sheet being composed.
        start: The line's left end, at the height it marks.
        length: The line's length; at least as long as the text and the triangle.
        text: The level as printed, such as ``+3,35``.
        size: The text's size.

    Returns:
        The box of what was drawn, and the line.
    """
    x, y = start
    half, height = LEVEL_TRIANGLE_MM
    tip = x + 4.0
    width = text_width(text, size)
    end = max(x + length, tip + half + 1.0 + width)
    sheet.line(x, y, end, y, width=MEDIUM_MM)
    sheet.polyline(
        [(tip - half, y + height), (tip + half, y + height), (tip, y)],
        width=FINE_MM,
        fill="#fff",
        closed=True,
    )
    sheet.text(tip + half + 1.0, y + 0.6, text, size=size)
    box = (x, y - 0.5, end, y + max(height, 0.6 + 0.76 * size))
    return box, [(x, y), (end, y)]


#: A section mark's bubble radius, and the length of its arrow beyond the bubble.
MARK_RADIUS_MM, MARK_ARROW_MM = 4.5, 3.5


def draw_section_mark(
    sheet: Sheet,
    centre: Point,
    toward: Point,
    looking: Point,
    mark: SectionMark,
) -> tuple[Box, list[Point]]:
    """Draw one end of a section line: a heavy stub, a bubble, and an arrow.

    The bubble prints the section's name over the sheet it is drawn on. The arrow is a
    filled triangle on the bubble's side facing the way the section looks.

    Args:
        sheet: The sheet being composed.
        centre: The bubble's centre.
        toward: Where the stub runs to from the bubble: the edge of the building.
        looking: The unit direction the section looks, on the paper.
        mark: The section.

    Returns:
        The box of everything drawn, and the stub as a line.
    """
    dx, dy = toward[0] - centre[0], toward[1] - centre[1]
    length = math.hypot(dx, dy) or 1.0
    start = (centre[0] + dx / length * MARK_RADIUS_MM, centre[1] + dy / length * MARK_RADIUS_MM)
    sheet.line(*start, *toward, width=HEAVY_MM)
    lx, ly = looking
    base = (centre[0] + lx * (MARK_RADIUS_MM - 0.4), centre[1] + ly * (MARK_RADIUS_MM - 0.4))
    tip = (
        centre[0] + lx * (MARK_RADIUS_MM + MARK_ARROW_MM),
        centre[1] + ly * (MARK_RADIUS_MM + MARK_ARROW_MM),
    )
    side = (-ly * MARK_RADIUS_MM * 0.75, lx * MARK_RADIUS_MM * 0.75)
    triangle = [
        (base[0] + side[0], base[1] + side[1]),
        tip,
        (base[0] - side[0], base[1] - side[1]),
    ]
    sheet.polyline(triangle, width=0, fill="#000", closed=True)
    sheet.circle(*centre, MARK_RADIUS_MM, width=MEDIUM_MM)
    sheet.line(
        centre[0] - MARK_RADIUS_MM, centre[1], centre[0] + MARK_RADIUS_MM, centre[1], width=FINE_MM
    )
    sheet.text(centre[0], centre[1] + 1.1, mark.label, size=2.8, anchor="middle", bold=True)
    sheet.text(centre[0], centre[1] - 2.6, mark.target_sheet, size=1.5, anchor="middle")
    box = bounding_box(
        [
            (centre[0] - MARK_RADIUS_MM, centre[1] - MARK_RADIUS_MM),
            (centre[0] + MARK_RADIUS_MM, centre[1] + MARK_RADIUS_MM),
            *triangle,
            start,
            toward,
        ]
    )
    return box, [start, toward]


#: A view title's bubble radius, the title's size and the scale's size.
TITLE_RADIUS_MM, TITLE_SIZE_MM, TITLE_SCALE_MM = 6.0, 4.5, 3.0


def draw_view_title(
    sheet: Sheet, left: float, baseline: float, title: ViewTitle, scale: str
) -> tuple[Box, Box | None, Box]:
    """Draw a view's title: its bubble, if it has one, its name underlined, and its scale.

    Args:
        sheet: The sheet being composed.
        left: Where the title starts on the paper.
        baseline: The name's baseline.
        title: The title.
        scale: The scale as printed, such as ``1:100``.

    Returns:
        The box of the name, of the bubble (None without one), and of the scale.
    """
    bubble: Box | None = None
    x = left
    if title.label is not None:
        centre = (left + TITLE_RADIUS_MM, baseline + TITLE_SIZE_MM * 0.35)
        sheet.circle(*centre, TITLE_RADIUS_MM, width=MEDIUM_MM)
        sheet.line(
            centre[0] - TITLE_RADIUS_MM,
            centre[1],
            centre[0] + TITLE_RADIUS_MM,
            centre[1],
            width=FINE_MM,
        )
        sheet.text(centre[0], centre[1] + 1.4, title.label, size=3.5, anchor="middle", bold=True)
        if title.target_sheet:
            sheet.text(centre[0], centre[1] - 3.3, title.target_sheet, size=1.8, anchor="middle")
        bubble = (
            centre[0] - TITLE_RADIUS_MM,
            centre[1] - TITLE_RADIUS_MM,
            centre[0] + TITLE_RADIUS_MM,
            centre[1] + TITLE_RADIUS_MM,
        )
        x = bubble[2] + 3.0
    width = text_width(title.text, TITLE_SIZE_MM, bold=True)
    sheet.text(x, baseline, title.text, size=TITLE_SIZE_MM, bold=True)
    rule = baseline - 1.8
    sheet.line(left if bubble is None else bubble[2], rule, x + width, rule, width=HEAVY_MM)
    sheet.text(x, rule - 1.2 - TITLE_SCALE_MM * 0.76, scale, size=TITLE_SCALE_MM)
    name = (x, baseline - 0.22 * TITLE_SIZE_MM, x + width, baseline + 0.76 * TITLE_SIZE_MM)
    scale_box = (
        x,
        rule - 1.2 - TITLE_SCALE_MM * 0.98,
        x + text_width(scale, TITLE_SCALE_MM),
        rule - 1.2,
    )
    return name, bubble, scale_box


#: The north arrow's radius.
NORTH_RADIUS_MM = 7.0


def draw_north_arrow(sheet: Sheet, centre: Point, north: Point) -> tuple[Box, list[Point]]:
    """Draw a north arrow: a circle, and a half-filled arrow pointing at true north.

    Args:
        sheet: The sheet being composed.
        centre: The circle's centre.
        north: The unit direction of true north on the paper.

    Returns:
        The box of what was drawn, and the arrow's axis from tail to head.
    """
    nx, ny = north
    radius = NORTH_RADIUS_MM
    sheet.circle(*centre, radius, width=FINE_MM)
    head = (centre[0] + nx * radius * 1.25, centre[1] + ny * radius * 1.25)
    tail = (centre[0] - nx * radius * 0.85, centre[1] - ny * radius * 0.85)
    side = (-ny * radius * 0.42, nx * radius * 0.42)
    left = [head, (tail[0] + side[0], tail[1] + side[1]), centre]
    right = [head, (tail[0] - side[0], tail[1] - side[1]), centre]
    sheet.polyline(left, width=FINE_MM, fill="#000", closed=True)
    sheet.polyline(right, width=FINE_MM, fill="#fff", closed=True)
    label = (centre[0] + nx * (radius * 1.25 + 3.2), centre[1] + ny * (radius * 1.25 + 3.2) - 1.3)
    sheet.text(*label, "N", size=3.5, anchor="middle", bold=True)
    box = bounding_box(
        [
            (centre[0] - radius, centre[1] - radius),
            (centre[0] + radius, centre[1] + radius),
            head,
            *left,
            *right,
            (label[0] - 1.5, label[1] - 0.8),
            (label[0] + 1.5, label[1] + 2.7),
        ]
    )
    return box, [tail, head]


def draw_scale_bar(
    sheet: Sheet, left: float, bottom: float, scale_denominator: float
) -> tuple[Box, list[Point], str]:
    """Draw a scale bar: 0 to 5 metres in one-metre blocks, then on to 10.

    Args:
        sheet: The sheet being composed.
        left: Where the bar starts on the paper.
        bottom: Its lower edge.
        scale_denominator: The drawing scale's denominator.

    Returns:
        The box of what was drawn, the bar's axis, and its text.
    """
    per_metre = 1000.0 / scale_denominator
    height = 1.5
    for index, (start, end) in enumerate([*((m, m + 1) for m in range(5)), (5, 10)]):
        x0, x1 = left + start * per_metre, left + end * per_metre
        fill = "#000" if index % 2 == 0 else "#fff"
        sheet.polyline(
            [(x0, bottom), (x1, bottom), (x1, bottom + height), (x0, bottom + height)],
            width=FINE_MM,
            fill=fill,
            closed=True,
        )
    marks = [0, 1, 2, 3, 4, 5, 10]
    for metres in marks:
        text = f"{metres} m" if metres == marks[-1] else str(metres)
        sheet.text(
            left + metres * per_metre, bottom + height + 1.2, text, size=2.0, anchor="middle"
        )
    right = left + 10 * per_metre
    box = (left - 1.0, bottom, right + text_width(" 10 m", 2.0) / 2.0 + 1.0, bottom + height + 3.0)
    return box, [(left, bottom), (right, bottom)], " ".join(str(m) for m in marks) + " m"
