# SPDX-License-Identifier: Apache-2.0
"""Reconstruct a page label from what is printed on the page.

Every value here is read off the drawing, so every value is ``inferred`` and carries a
``confidence`` (SPEC 4.6). The confidences are not calibrated probabilities -- SPEC
4.6.5 says a reader must not compare them across writers -- but they are ordered: a
grid read from a letter inside a circle at the end of a line is surer than a class
guessed from a tag's prefix, and the numbers say so.

The order matters. Grid bubbles are found first, because a digit inside a bubble is an
axis and a digit beside a line is a dimension, and the same character is both until the
geometry decides. Everything a bubble claims is then off the table for the passes after.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from plannotation.constants import SCHEMA_VERSION
from plannotation.infer.patterns import (
    DRAWING_TYPES,
    GRID_AXIS,
    REVISION,
    SCALE,
    SHEET_ID,
    STRUCTURAL_TYPES,
    parse_dimension,
    parse_level,
    tag_family,
)
from plannotation.model import (
    Annotation,
    Element,
    Generator,
    Page,
    PageLabel,
    Provenance,
    Sheet,
    Shows,
    Target,
)
from plannotation.units import COORD_DECIMALS

if TYPE_CHECKING:
    from plannotation.model import Discipline, DrawingType
    from plannotation.pdf.extract import Circle, PageContent, Segment, Word

#: How far a word's centre may sit from a circle's centre, as a fraction of the radius,
#: and still be read as the text inside it.
INSIDE_CIRCLE = 0.8

#: How far a grid line's end may be from its bubble's edge, in millimetres.
GRID_LINE_REACH_MM = 20.0

#: How far a dimension's number may sit from the line it labels, in millimetres.
DIMENSION_REACH_MM = 6.0

#: How far below a level's text its line may run, in paper millimetres: the text sits
#: on the line it names.
LEVEL_REACH_MM = 3.0

#: The fraction of the page, measured from the bottom-right corner, searched for the
#: title block. Drawing offices put it there by convention, and nearly all of them.
TITLE_BLOCK_REGION = 0.45

#: Fewest points a line needs to have two ends.
_MIN_LINE_POINTS = 2

#: How close a dimension line's end must be to a grid line, in millimetres, for the
#: dimension to be read as measuring to that grid.
GRID_ALIGNMENT_MM = 1.5

#: How big a circle may be and still be a grid bubble rather than a callout.
MAX_BUBBLE_RADIUS_MM = 5.0


def _round_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Round a box to the serialised precision.

    Args:
        box: ``(x0, y0, x1, y1)``.

    Returns:
        The same box, rounded.
    """
    return (
        round(box[0], COORD_DECIMALS),
        round(box[1], COORD_DECIMALS),
        round(box[2], COORD_DECIMALS),
        round(box[3], COORD_DECIMALS),
    )


def _union(*boxes: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Return the box containing every box given.

    Args:
        *boxes: Boxes in paper millimetres.

    Returns:
        Their union.
    """
    return _round_box(
        (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
    )


def _inside(word: Word, circle: Circle) -> bool:
    """Report whether a word sits inside a circle.

    Args:
        word: The word.
        circle: The circle.

    Returns:
        True when the word's centre is well within the circle.
    """
    dx = word.centre[0] - circle.centre[0]
    dy = word.centre[1] - circle.centre[1]
    return float((dx * dx + dy * dy) ** 0.5) <= circle.radius * INSIDE_CIRCLE


def _distance_to_segment(point: tuple[float, float], segment: Segment) -> float:
    """Return the shortest distance from a point to a line segment.

    Args:
        point: The point, in paper millimetres.
        segment: The segment.

    Returns:
        The distance in millimetres.
    """
    (x0, y0), (x1, y1) = segment.start, segment.end
    dx, dy = x1 - x0, y1 - y0
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return float(((point[0] - x0) ** 2 + (point[1] - y0) ** 2) ** 0.5)
    t = max(0.0, min(1.0, ((point[0] - x0) * dx + (point[1] - y0) * dy) / length_squared))
    closest = (x0 + t * dx, y0 + t * dy)
    return float(((point[0] - closest[0]) ** 2 + (point[1] - closest[1]) ** 2) ** 0.5)


def infer_page(content: PageContent, *, page_index: int, generator_version: str) -> PageLabel:
    """Reconstruct one page's label from its printed content.

    Args:
        content: The page's words, lines and circles.
        page_index: The zero-based page index.
        generator_version: The version to record in ``generator``.

    Returns:
        A label whose every item is ``inferred`` and carries a confidence.
    """
    used: set[int] = set()
    annotations: list[Annotation] = []

    grids = _grids(content, used)
    annotations += grids
    levels = _levels(content, used)
    annotations += levels
    marked = [*grids, *levels]
    annotations += _link_dimensions(_dimensions(content, used, marked), grids, levels)
    elements, tags = _tags(content, used)
    annotations += tags
    annotations += _callouts(content, used)

    sheet = _sheet(content, used)
    return PageLabel(
        plannotation=SCHEMA_VERSION,
        generator=Generator(name="plannotation-infer", version=generator_version),
        provenance=Provenance.INFERRED,
        page=Page(index=page_index, widthMm=content.width_mm, heightMm=content.height_mm),
        sheet=sheet,
        elements=elements or None,
        annotations=annotations or None,
    )


def _grids(content: PageContent, used: set[int]) -> list[Annotation]:
    """Find grid bubbles: a letter or number inside a small circle at a line's end.

    Args:
        content: The page.
        used: Indexes of words already claimed, updated in place.

    Returns:
        One ``grid`` annotation per bubble.
    """
    found: list[Annotation] = []
    for circle in content.circles:
        if circle.radius > MAX_BUBBLE_RADIUS_MM:
            continue
        for index, word in enumerate(content.words):
            if index in used or not GRID_AXIS.match(word.text) or not _inside(word, circle):
                continue
            bubble = (
                circle.centre[0] - circle.radius,
                circle.centre[1] - circle.radius,
                circle.centre[0] + circle.radius,
                circle.centre[1] + circle.radius,
            )
            line = _grid_line(content, circle)
            confidence = 0.95 if line is not None else 0.7
            box = bubble if line is None else _union(bubble, _segment_box(line))
            found.append(
                Annotation(
                    id=f"g-{word.text}",
                    type="grid",
                    paperBBox=_round_box(box),
                    text=word.text,
                    axis=word.text,
                    geometry=[line.start, line.end] if line is not None else None,
                    provenance=Provenance.INFERRED,
                    confidence=confidence,
                )
            )
            used.add(index)
            break
    return found


def _grid_line(content: PageContent, circle: Circle) -> Segment | None:
    """Return the long line whose end sits nearest a bubble, if one is close enough.

    Args:
        content: The page.
        circle: The bubble.

    Returns:
        The grid line, or None.
    """
    best: tuple[float, Segment] | None = None
    for segment in content.segments:
        for end in (segment.start, segment.end):
            gap = (
                float(((end[0] - circle.centre[0]) ** 2 + (end[1] - circle.centre[1]) ** 2) ** 0.5)
                - circle.radius
            )
            if gap <= GRID_LINE_REACH_MM and (best is None or gap < best[0]):
                best = (gap, segment)
    return best[1] if best is not None else None


def _segment_box(segment: Segment) -> tuple[float, float, float, float]:
    """Return a segment's bounding box.

    Args:
        segment: The segment.

    Returns:
        Its box, ordered.
    """
    return _round_box(
        (
            min(segment.start[0], segment.end[0]),
            min(segment.start[1], segment.end[1]),
            max(segment.start[0], segment.end[0]),
            max(segment.start[1], segment.end[1]),
        )
    )


def _levels(content: PageContent, used: set[int]) -> list[Annotation]:
    """Find level marks: a signed elevation printed on a horizontal line.

    A section marks each storey with its elevation, ``±0,00`` or ``+3,00``, written on
    the level line. The sign is what tells a level from a dimension. Finding levels
    before dimensions matters for the dimensions too: a storey height runs between two
    level lines, and can only be linked to them once they are known.

    Args:
        content: The page.
        used: Indexes of words already claimed, updated in place.

    Returns:
        One ``level`` annotation per mark with a line under it.
    """
    found: list[Annotation] = []
    for index, word in enumerate(content.words):
        if index in used:
            continue
        elevation = parse_level(word.text)
        if elevation is None:
            continue
        under = [
            segment
            for segment in content.segments
            if abs(segment.start[1] - segment.end[1]) < GRID_ALIGNMENT_MM
            and 0.0 <= word.bbox[1] - segment.start[1] <= LEVEL_REACH_MM
            and min(segment.start[0], segment.end[0]) <= word.bbox[2]
            and max(segment.start[0], segment.end[0]) >= word.bbox[0]
        ]
        if not under:
            continue
        line = max(under, key=lambda segment: segment.length)
        found.append(
            Annotation(
                id=f"lvl-{len(found) + 1:02d}",
                type="level",
                paperBBox=_union(word.bbox, _segment_box(line)),
                text=word.text,
                elevation=elevation,
                geometry=[line.start, line.end],
                provenance=Provenance.INFERRED,
                confidence=0.85,
            )
        )
        used.add(index)
    return found


def _dimensions(content: PageContent, used: set[int], marked: list[Annotation]) -> list[Annotation]:
    """Find dimensions: a number printed beside a line.

    The unit is taken as millimetres, which is what a building drawing dimensions in by
    default, and the confidence is kept below a grid's to say so. A number without a
    line near it is not reported: that is the likelier reading of a stray figure in a
    note than of a dimension whose line was lost.

    The line is the nearest one that is not already a grid's or a level's. An overall
    dimension's value often prints across a grid line, and taking that line for the
    dimension's would make it measure nothing.

    Args:
        content: The page.
        used: Indexes of words already claimed, updated in place.
        marked: The grids and levels already found, whose lines are theirs.

    Returns:
        One ``dimension`` annotation per number with a line beside it.
    """
    taken = {
        (round(point[0], 2), round(point[1], 2))
        for annotation in marked
        for point in (annotation.geometry or [])
    }

    def owned(segment: Segment) -> bool:
        ends = {(round(p[0], 2), round(p[1], 2)) for p in (segment.start, segment.end)}
        return ends <= taken

    found: list[Annotation] = []
    for index, word in enumerate(content.words):
        if index in used:
            continue
        value = parse_dimension(word.text)
        if value is None:
            continue
        near = [
            segment
            for segment in content.segments
            if _distance_to_segment(word.centre, segment) <= DIMENSION_REACH_MM
            and segment.length > (word.bbox[2] - word.bbox[0])
            and not owned(segment)
        ]
        if not near:
            continue
        line = min(near, key=lambda segment: _distance_to_segment(word.centre, segment))
        found.append(
            Annotation(
                id=f"a-dim-{len(found) + 1:02d}",
                type="dimension",
                paperBBox=_union(word.bbox, _segment_box(line)),
                text=word.text,
                value=value,
                unit="mm",
                geometry=[line.start, line.end],
                provenance=Provenance.INFERRED,
                confidence=0.75,
            )
        )
        used.add(index)
    return found


def _link_dimensions(
    dimensions: list[Annotation], grids: list[Annotation], levels: list[Annotation]
) -> list[Annotation]:
    """Say which grids or levels each dimension measures between, where it shows.

    A running dimension between two grid lines ends on them: a horizontal dimension
    line starts and stops at the x of two vertical grids. Where both ends of a
    dimension line sit on a grid line, the dimension ``measures`` those two grids --
    which is what lifts it from a number beside a line to a statement about the
    building, and what the validator asks of a dimension (PL-REF-011).

    A dimension whose ends do not both land on grid lines is left unlinked rather than
    linked to the nearest grids, because a dimension to a wall face measured as though
    it ran grid to grid would state the wrong distance between the wrong things.

    A dimension running up the page ends on two horizontal lines, which are grid
    lines on a plan and level lines on a section; both are looked for.

    Args:
        dimensions: The dimensions found.
        grids: The grids found, with their lines as ``geometry``.
        levels: The levels found, with their lines as ``geometry``.

    Returns:
        The dimensions, with ``measures`` filled in where both ends align.
    """
    vertical: list[tuple[float, str]] = []
    horizontal: list[tuple[float, str]] = []
    for grid in grids:
        if not grid.geometry or len(grid.geometry) < _MIN_LINE_POINTS:
            continue
        (x0, y0), (x1, y1) = grid.geometry[0], grid.geometry[-1]
        if abs(x1 - x0) < abs(y1 - y0):
            vertical.append(((x0 + x1) / 2.0, grid.local_id))
        else:
            horizontal.append(((y0 + y1) / 2.0, grid.local_id))
    for level in levels:
        if level.geometry and len(level.geometry) >= _MIN_LINE_POINTS:
            (_, y0), (_, y1) = level.geometry[0], level.geometry[-1]
            horizontal.append(((y0 + y1) / 2.0, level.local_id))

    linked: list[Annotation] = []
    for dimension in dimensions:
        ends = dimension.geometry or []
        if len(ends) < _MIN_LINE_POINTS:
            linked.append(dimension)
            continue
        (x0, y0), (x1, y1) = ends[0], ends[-1]
        across = abs(x1 - x0) >= abs(y1 - y0)
        lines, first, second = (vertical, x0, x1) if across else (horizontal, y0, y1)
        start = _grid_at(lines, first)
        stop = _grid_at(lines, second)
        if start is not None and stop is not None and start != stop:
            linked.append(dimension.model_copy(update={"measures": [start, stop]}))
        else:
            linked.append(dimension)
    return linked


def _grid_at(lines: list[tuple[float, str]], position: float) -> str | None:
    """Return the grid whose line runs through a position, if one does.

    Args:
        lines: ``(coordinate, grid id)`` for grid lines of one orientation.
        position: The coordinate to look at.

    Returns:
        The grid's id, or None when no line is close enough.
    """
    near = [(abs(coordinate - position), grid) for coordinate, grid in lines]
    if not near:
        return None
    distance, grid = min(near)
    return grid if distance <= GRID_ALIGNMENT_MM else None


def _tags(content: PageContent, used: set[int]) -> tuple[list[Element], list[Annotation]]:
    """Find marks, and an element for each one.

    A mark is the one thing on a drawing that names an element; the element itself is
    not otherwise identifiable from the page. Its IFC class is a guess from the mark's
    family, and its box is the mark's own box, because the drawn element around it
    cannot be told from its neighbours without the model. Both facts are in the
    confidence.

    Args:
        content: The page.
        used: Indexes of words already claimed, updated in place.

    Returns:
        The candidate elements and the ``tag`` annotations that show them.
    """
    elements: list[Element] = []
    tags: list[Annotation] = []
    words = list(enumerate(content.words))
    position = 0
    while position < len(words):
        index, word = words[position]
        text, box, consumed = word.text, word.bbox, [index]
        family = tag_family(text)
        # A mark printed with a space -- "Pos. 3" -- arrives as two words.
        if family is None and position + 1 < len(words):
            next_index, next_word = words[position + 1]
            joined = f"{text} {next_word.text}"
            if tag_family(joined) is not None:
                family = tag_family(joined)
                text, box = joined, _union(box, next_word.bbox)
                consumed.append(next_index)
                position += 1
        position += 1
        if family is None or any(item in used for item in consumed):
            continue
        ifc_class, confidence = family
        element_id = f"e-{len(elements) + 1:02d}"
        elements.append(
            Element(
                id=element_id,
                ifcClass=ifc_class,
                tag=text,
                paperBBox=_round_box(box),
                representation="symbol",
                provenance=Provenance.INFERRED,
                confidence=confidence,
            )
        )
        tags.append(
            Annotation(
                id=f"a-tag-{len(tags) + 1:02d}",
                type="tag",
                paperBBox=_round_box(box),
                text=text,
                shows=Shows(element=element_id, property="Tag"),
                provenance=Provenance.INFERRED,
                confidence=0.9,
            )
        )
        used.update(consumed)
    return elements, tags


def _callouts(content: PageContent, used: set[int]) -> list[Annotation]:
    """Find callouts: a sheet number inside a circle too big to be a grid bubble.

    Args:
        content: The page.
        used: Indexes of words already claimed, updated in place.

    Returns:
        One ``callout`` annotation per callout.
    """
    found: list[Annotation] = []
    for circle in content.circles:
        if circle.radius <= MAX_BUBBLE_RADIUS_MM:
            continue
        for index, word in enumerate(content.words):
            if index in used or not SHEET_ID.match(word.text) or not _inside(word, circle):
                continue
            found.append(
                Annotation(
                    id=f"a-call-{len(found) + 1:02d}",
                    type="callout",
                    paperBBox=_round_box(
                        (
                            circle.centre[0] - circle.radius,
                            circle.centre[1] - circle.radius,
                            circle.centre[0] + circle.radius,
                            circle.centre[1] + circle.radius,
                        )
                    ),
                    text=word.text,
                    target=Target(sheetId=word.text),
                    provenance=Provenance.INFERRED,
                    confidence=0.8,
                )
            )
            used.add(index)
            break
    return found


def _sheet(content: PageContent, used: set[int]) -> Sheet:
    """Read the title block: sheet number, title, scale, revision and drawing type.

    Words already claimed are skipped. A callout in the bottom-right corner prints a
    sheet number inside its circle, and that number is the sheet it points *at*; read
    as this sheet's own number, it would name the wrong drawing. That is the failure
    this skip exists for, and it was found on the project's own floor plan.

    Args:
        content: The page.
        used: Indexes of words already claimed by grids, dimensions, tags and callouts.

    Returns:
        The sheet block. The sheet number falls back to ``UNKNOWN`` rather than to a
        guess, because a wrong sheet number is worse than an admitted missing one.
    """
    left = content.width_mm * (1.0 - TITLE_BLOCK_REGION)
    top = content.height_mm * TITLE_BLOCK_REGION
    region = [
        word
        for index, word in enumerate(content.words)
        if index not in used and word.centre[0] >= left and word.centre[1] <= top
    ]

    sheet_id = next((word.text for word in region if SHEET_ID.match(word.text)), None)
    scale = None
    for position, word in enumerate(region):
        for candidate in (
            word.text,
            f"{word.text} {region[position + 1].text}" if position + 1 < len(region) else word.text,
        ):
            match = SCALE.match(candidate)
            if match is not None:
                scale = float(match.group(1))
                break
        if scale is not None:
            break
    revision = next(
        (match.group(1) for word in region if (match := REVISION.match(word.text))), None
    )
    title_words = [
        word
        for word in region
        if word.text not in {sheet_id}
        and not SCALE.match(word.text)
        and not REVISION.match(word.text)
        and not any(ch.isdigit() for ch in word.text)
    ]
    title = _title_line(title_words)
    drawing_type = _drawing_type(title)
    discipline = (
        "structure"
        if drawing_type in STRUCTURAL_TYPES
        else "architecture"
        if drawing_type
        else None
    )
    block = None
    if region:
        block = _union(*(word.bbox for word in region))
    return Sheet(
        id=sheet_id or "UNKNOWN",
        title=title,
        revision=revision,
        scale=scale,
        drawingType=cast("DrawingType | None", drawing_type),
        discipline=cast("Discipline | None", discipline),
        titleBlockBBox=block,
    )


def _title_line(words: list[Word]) -> str | None:
    """Return the line of the title block most likely to be the sheet title.

    Words are grouped into lines by their baseline, and the title is taken to be the
    line containing a drawing-type word, falling back to the longest line.

    Args:
        words: Candidate words from the title block.

    Returns:
        The title, or None when the block holds no plausible line.
    """
    lines: dict[float, list[Word]] = {}
    for word in words:
        lines.setdefault(round(word.bbox[1], 0), []).append(word)
    texts = [
        " ".join(word.text for word in sorted(line, key=lambda w: w.bbox[0]))
        for line in lines.values()
    ]
    if not texts:
        return None
    for text in texts:
        if _drawing_type(text):
            return text
    return max(texts, key=len)


def _drawing_type(title: str | None) -> str | None:
    """Classify a drawing by the words in its title.

    Args:
        title: The sheet title.

    Returns:
        A ``drawingType`` value, or None when no keyword matches.
    """
    if not title:
        return None
    lowered = title.casefold()
    return next((kind for keyword, kind in DRAWING_TYPES if keyword in lowered), None)
