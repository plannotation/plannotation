# SPDX-License-Identifier: Apache-2.0
"""The authored exporter: a model in, a labelled drawing out.

Every value this writes is computed from the model, which is what makes the output
``authored`` rather than ``inferred`` (SPEC 4.6). A dimension's value is the distance
between two grid lines, or two storeys, as the model places them, not a number
measured off a picture; an element's ``paperBBox`` is its drawn geometry through the
verified paper transform; whether it is cut or seen beyond the cut comes from its
geometry in the model; a tag prints the ``Tag`` the model holds, and a member's
cross-section is the ``Reference`` of its common property set. Nothing here reads the
drawing to find out what the drawing says.

The model's length unit is read from the model, never assumed: ``model.lengthUnit``,
``paperToPlane``, ``plane`` and ``cutHeight`` are all in it (SPEC 3.5), and a label that
named one unit while the file used another would put every coordinate a factor of a
thousand out.

The sheet is composed rather than rendered whole: the serializer draws the building --
a plan cut, or a vertical section -- and this module puts a frame, a title block, grid
lines and bubbles, level marks, dimensions, tags and a callout around it. Those are
the parts a label has something to say about, and a sample without them would
exercise nothing beyond L2.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass, field
from html import escape
from itertools import pairwise
from typing import TYPE_CHECKING, Any, cast, get_args

from plannotation.constants import SCHEMA_VERSION
from plannotation.errors import ExportError
from plannotation.export.geometry import bounding_box, path_points, union_box
from plannotation.export.paper import Affine, invert, paper_to_plane, plane_from_ifc_plane
from plannotation.export.sheet import PAPER_SIZES, Sheet, frame_box, title_block_box
from plannotation.export.svg_render import RenderedView, render_view
from plannotation.export.to_pdf import svg_to_pdf
from plannotation.model import (
    Annotation,
    DrawingType,
    Element,
    Generator,
    Model,
    Page,
    PageLabel,
    Plane,
    Project,
    Provenance,
    Representation,
    Shows,
    Storey,
    Target,
    Viewport,
    canonical_json,
)
from plannotation.model import (
    Sheet as SheetInfo,
)
from plannotation.pdf import embed
from plannotation.svg.carrier import uuid_from_guid
from plannotation.units import length_unit_for

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    from plannotation.export.models import BuiltModel, Level
    from plannotation.model import Discipline

#: Where the drawing sits on the sheet, as a paper bounding box on an A3 page.
VIEWPORT_BOX = (25.0, 85.0, 325.0, 285.0)

#: How far short of the viewport's edge a grid line stops, leaving room for its bubble
#: inside the sheet frame.
BUBBLE_OFFSET_MM = 8.0

#: A grid bubble's radius, in paper millimetres.
BUBBLE_RADIUS_MM = 4.0

#: How far from the drawing the first dimension chain runs, and the second: below it
#: for lengths along the page, to its right for lengths up it.
DIMENSION_OFFSETS_MM = (10.0, 22.0)

#: Paper millimetres per character of a dimension's printed value, for its box.
_DIMENSION_CHAR_MM = 2.0

#: Half the width and the height of a mark as printed, for its box and for spacing marks.
TAG_HALF_WIDTH_MM = 8.0
TAG_BELOW_MM, TAG_ABOVE_MM = 1.5, 2.5

#: How far below a mark its cross-section prints, and how tall that line is.
REFERENCE_DROP_MM = 3.2
REFERENCE_HEIGHT_MM = 3.0

#: How far a level mark's line reaches left of the drawing, and its half height.
LEVEL_LENGTH_MM, LEVEL_HALF_HEIGHT_MM = 22.0, 3.0

#: Fewest points a path must have before it is worth recording as an outline.
_MIN_OUTLINE_POINTS = 2

#: How far outside a cutting plane an element may reach and still count as cut, in
#: metres: the tolerance that stops a slab whose top face *is* the cut from flickering.
_CUT_TOLERANCE_M = 1e-6

#: One line of ``groundtruth.jsonl``: a question, its category, and its answer.
Question = dict[str, object]

#: The element classes the samples draw, in the order their counts are asked about.
#: Walls are always asked about, so a sheet without any has a count of zero to get right.
_PLURAL = {
    "IfcWall": "walls",
    "IfcDoor": "doors",
    "IfcWindow": "windows",
    "IfcColumn": "columns",
    "IfcBeam": "beams",
    "IfcSlab": "slabs",
}

#: Every drawing kind the schema knows, offered as the choices for that question.
_DRAWING_TYPES: tuple[str, ...] = get_args(DrawingType)


@dataclass(frozen=True)
class GridAxis:
    """One grid line, placed in the model and drawn on the paper.

    Attributes:
        axis: The label printed in the bubble, such as ``A`` or ``1``.
        vertical: True for a line of constant plane x, drawn up the page.
        position: Its plane coordinate along the axis it is constant in, in metres.
    """

    axis: str
    vertical: bool
    position: float


@dataclass(frozen=True)
class SheetSpec:
    """What is drawn on one sheet, and what the sheet says about itself.

    Attributes:
        sheet_id: The sheet number as it prints.
        title: The sheet title as it prints.
        scale: The drawing scale's denominator.
        grids: The grid lines to draw and dimension between.
        callout_to: The sheet the callout points at.
        drawing_type: What kind of drawing this is. It is stated rather than assumed:
            a position plan labelled as an architectural plan is a false statement
            about the sheet, and it is exactly the one inference caught.
        discipline: The discipline the drawing belongs to.
    """

    sheet_id: str
    title: str
    scale: float
    grids: tuple[GridAxis, ...]
    callout_to: str
    drawing_type: DrawingType = "plan"
    discipline: Discipline = "architecture"


@dataclass(frozen=True)
class ExportedSheet:
    """Everything one exported sheet produced.

    Attributes:
        svg: The composed sheet.
        label: The page label describing it.
        ground_truth: Questions with answers taken from the model.
    """

    svg: str
    label: PageLabel
    ground_truth: tuple[Question, ...]


@dataclass(frozen=True)
class ModelFacts:
    """What the exporter reads from the model rather than from the drawing.

    Attributes:
        unit_scale: The model's length unit in metres.
        classes: Each building element's IFC class and name, by GlobalId.
        tags: Each product's ``Tag``, by GlobalId.
        properties: Each product's common property sets, by GlobalId.
        extents: Each product's world bounding box in metres, by GlobalId.
    """

    unit_scale: float
    classes: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    properties: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    extents: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Frame:
    """Where one view sits and how its paper maps to the model.

    Attributes:
        viewport: The viewport's local id.
        transform: Its ``paperToPlane``.
        unit_scale: The model's length unit in metres.
        origin_z: The plane origin's height, in model units.
    """

    viewport: str
    transform: Affine
    unit_scale: float
    origin_z: float

    def paper_x(self, plane_x_m: float) -> float:
        """Return the paper x of a line of constant plane x, given in metres.

        Args:
            plane_x_m: The plane coordinate in metres.

        Returns:
            Paper x in millimetres.
        """
        return invert(self.transform, plane_x_m / self.unit_scale, 0.0)[0]

    def paper_y(self, plane_y_m: float) -> float:
        """Return the paper y of a line of constant plane y, given in metres.

        Args:
            plane_y_m: The plane coordinate in metres.

        Returns:
            Paper y in millimetres.
        """
        return invert(self.transform, 0.0, plane_y_m / self.unit_scale)[1]


def export_sheet(
    built: BuiltModel,
    spec: SheetSpec,
    *,
    generator_version: str,
    page_size: str = "A3",
) -> ExportedSheet:
    """Draw one model on one sheet and describe it.

    Args:
        built: The model to draw.
        spec: What the sheet is and what is drawn on it.
        generator_version: The version to record in ``generator``.
        page_size: A key of :data:`plannotation.export.sheet.PAPER_SIZES`.

    Returns:
        The composed sheet, its label, and its ground truth.

    Raises:
        ExportError: If the view draws no element.
    """
    width_mm, height_mm = PAPER_SIZES[page_size]
    facts = read_model_facts(built.path)
    view = render_view(
        built.path,
        scale_denominator=spec.scale,
        width_mm=VIEWPORT_BOX[2] - VIEWPORT_BOX[0],
        height_mm=VIEWPORT_BOX[3] - VIEWPORT_BOX[1],
        section_height=built.cut_height,
        section=built.section,
        name=spec.title,
    )

    sheet = Sheet(width_mm=width_mm, height_mm=height_mm)
    sheet.rect(frame_box(width_mm, height_mm))
    # The serializer draws into its own box, whose top-left goes at the viewport's
    # top-left on the sheet. In SVG that is a downward y, so the paper top edge.
    offset = (VIEWPORT_BOX[0], sheet.y(VIEWPORT_BOX[3]))

    transform = paper_to_plane(view.matrix3, height_mm, facts.unit_scale, svg_offset=offset)
    origin, x_axis, y_axis = plane_from_ifc_plane(view.ifc_plane, facts.unit_scale)
    frame = _Frame(
        viewport="vp-section" if built.section is not None else "vp-plan",
        transform=transform,
        unit_scale=facts.unit_scale,
        origin_z=origin[2],
    )

    elements = _elements(view, height_mm, offset, facts, built, frame.viewport)
    group = _view_group(view.svg)
    if built.section is None:
        seen, markup = _projections(
            facts, built, frame, (origin, x_axis, y_axis), elements, (*offset, height_mm)
        )
        elements += seen
        group = group[: -len("</g>")] + markup + "</g>"
    sheet.group(group, transform=f"translate({offset[0]},{offset[1]})")
    if not elements:
        msg = f"sheet {spec.sheet_id} drew no elements; the view would be empty"
        raise ExportError(msg)
    content = union_box(element.paper_bbox for element in elements)

    annotations: list[Annotation] = []
    annotations += _draw_grids(sheet, spec.grids, frame)
    levels = _draw_levels(sheet, built.levels, frame, left=content[0])
    annotations += levels
    annotations += _draw_grid_dimensions(sheet, spec.grids, frame, content)
    annotations += _draw_level_dimensions(sheet, levels, built.levels, content)
    annotations.append(_draw_callout(sheet, spec.callout_to, frame.viewport))
    annotations += _draw_tags(sheet, elements, frame.viewport, content, annotations)
    _draw_title_block(sheet, width_mm, height_mm, spec.sheet_id, spec.title, spec.scale)

    boxes = [element.paper_bbox for element in elements]
    boxes += [a.paper_bbox for a in annotations if a.viewport == frame.viewport]
    plan = built.section is None
    viewport = Viewport(
        id=frame.viewport,
        name=spec.title,
        kind="plan" if plan else "section",
        scale=spec.scale,
        paperBBox=union_box(boxes),
        plane=Plane(
            origin=(origin[0], origin[1], origin[2]),
            xAxis=(x_axis[0], x_axis[1], x_axis[2]),
            yAxis=(y_axis[0], y_axis[1], y_axis[2]),
        ),
        paperToPlane=transform,
        cutHeight=(built.cut_height or 0.0) / facts.unit_scale if plan else None,
        storey=(
            Storey(name="Erdgeschoss", elevation=built.storey_elevation / facts.unit_scale)
            if plan
            else None
        ),
    )
    label = PageLabel(
        plannotation=SCHEMA_VERSION,
        generator=Generator(name="plannotation", version=generator_version),
        provenance=Provenance.AUTHORED,
        page=Page(index=0, widthMm=width_mm, heightMm=height_mm),
        sheet=SheetInfo(
            id=spec.sheet_id,
            title=spec.title,
            revision="A",
            discipline=spec.discipline,
            drawingType=spec.drawing_type,
            scale=spec.scale,
            project=Project(name="Wohnanlage Lindenhof", number="2024-118"),
            titleBlockBBox=title_block_box(width_mm, height_mm),
        ),
        model=Model(
            file=built.path.name,
            lengthUnit=length_unit_for(facts.unit_scale),
            schema="IFC4",
        ),
        viewports=[viewport],
        elements=elements,
        annotations=annotations,
    )
    return ExportedSheet(
        svg=sheet.render(),
        label=label,
        ground_truth=tuple(_ground_truth(label, spec.sheet_id, spec.grids)),
    )


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
def read_model_facts(model_path: Path) -> ModelFacts:
    """Read the unit, marks, common property sets and extents out of a model.

    Args:
        model_path: The IFC file.

    Returns:
        The facts.
    """
    ifcopenshell = importlib.import_module("ifcopenshell")
    unit = importlib.import_module("ifcopenshell.util.unit")
    util_element = importlib.import_module("ifcopenshell.util.element")
    model = ifcopenshell.open(str(model_path))
    tags: dict[str, str] = {}
    properties: dict[str, dict[str, dict[str, object]]] = {}
    classes = {
        str(element.GlobalId): (str(element.is_a()), element.Name or None)
        for element in model.by_type("IfcBuildingElement")
    }
    for product in model.by_type("IfcProduct"):
        guid = str(product.GlobalId)
        if getattr(product, "Tag", None):
            tags[guid] = str(product.Tag)
        common = {
            name: {key: value for key, value in values.items() if key != "id"}
            for name, values in util_element.get_psets(product).items()
            if name.startswith("Pset_") and name.endswith("Common")
        }
        if common:
            properties[guid] = common
    return ModelFacts(
        unit_scale=float(unit.calculate_unit_scale(model)),
        classes=classes,
        tags=tags,
        properties=properties,
        extents=_extents(model),
    )


def _extents(model: Any) -> dict[str, tuple[tuple[float, ...], tuple[float, ...]]]:  # noqa: ANN401
    """Return every product's world bounding box, in metres.

    Args:
        model: The open ``ifcopenshell.file``.

    Returns:
        ``(minimum, maximum)`` corners by GlobalId.
    """
    geom = importlib.import_module("ifcopenshell.geom")
    settings = geom.settings()
    settings.set("use-world-coords", True)  # noqa: FBT003 - the wrapper is positional
    iterator = geom.iterator(settings, model, exclude=["IfcOpeningElement"])
    extents: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
    if iterator.initialize():
        while True:
            shape = iterator.get()
            verts = list(shape.geometry.verts)
            points = [verts[index : index + 3] for index in range(0, len(verts), 3)]
            if points:
                low = tuple(min(point[axis] for point in points) for axis in range(3))
                high = tuple(max(point[axis] for point in points) for axis in range(3))
                extents[str(shape.guid)] = (low, high)
            if not iterator.next():
                break
    return extents


def _representation(guid: str, facts: ModelFacts, built: BuiltModel) -> Representation | None:
    """Say whether the cut passes through an element or it is seen beyond it.

    Args:
        guid: The element's GlobalId.
        facts: What the model says.
        built: Where the drawing is cut.

    Returns:
        ``cut`` or ``projection``, or None when the model gives no geometry.
    """
    extent = facts.extents.get(guid)
    if extent is None:
        return None
    low, high = extent
    tolerance = _CUT_TOLERANCE_M
    if built.section is None:
        cut = built.storey_elevation + (built.cut_height or 0.0)
        return "cut" if low[2] - tolerance <= cut <= high[2] + tolerance else "projection"
    normal, point = built.section.direction, built.section.location
    corners = [
        (x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])
    ]
    distances = [
        sum(n * (c - p) for n, c, p in zip(normal, corner, point, strict=True))
        for corner in corners
    ]
    return "cut" if min(distances) <= tolerance and max(distances) >= -tolerance else "projection"


def _view_group(svg: str) -> str:
    """Return the serializer's view group, without the document around it.

    Args:
        svg: The serializer's output.

    Returns:
        The ``<g class="section">`` element and everything in it.

    Raises:
        ExportError: If there is no view group, which means nothing was drawn.
    """
    match = re.search(r'(<g\b[^>]*class="section".*</g>)', svg, flags=re.S)
    if match is None:
        msg = "the serializer produced no view group, so the sheet would be blank"
        raise ExportError(msg)
    return match.group(1)


def _elements(
    view: RenderedView,
    height_mm: float,
    offset: tuple[float, float],
    facts: ModelFacts,
    built: BuiltModel,
    viewport: str,
) -> list[Element]:
    """Describe every product the serializer drew.

    Args:
        view: The rendered view.
        height_mm: The sheet height, for the y-flip.
        offset: The wrapper translation that placed the view.
        facts: What the model says about each product.
        built: Where the view is cut.
        viewport: The viewport's local id.

    Returns:
        One element per product, with its paper geometry.
    """
    elements: list[Element] = []
    for product in view.products:
        outlines = [
            path_points(path, page_height_mm=height_mm, offset=offset) for path in product.paths
        ]
        points = [point for outline in outlines for point in outline]
        if not points:
            continue
        elements.append(
            Element(
                id=f"e-{len(elements):02d}",
                ifcGuid=product.guid,
                ifcClass=product.ifc_class,
                name=product.name,
                tag=facts.tags.get(product.guid),
                viewport=viewport,
                paperBBox=bounding_box(points),
                paperOutlines=[
                    [(point[0], point[1]) for point in outline]
                    for outline in outlines
                    if len(outline) >= _MIN_OUTLINE_POINTS
                ],
                representation=_representation(product.guid, facts, built),
                properties=cast("dict[str, Any] | None", facts.properties.get(product.guid)),
                provenance=Provenance.AUTHORED,
            )
        )
    return elements


def _projections(
    facts: ModelFacts,
    built: BuiltModel,
    frame: _Frame,
    plane: tuple[list[float], list[float], list[float]],
    drawn: list[Element],
    placement: tuple[float, float, float],
) -> tuple[list[Element], str]:
    """Draw what lies wholly below a plan's cut, which the serializer leaves out.

    The serializer draws what the cut passes through. A base slab under a
    foundation plan is below the cut, and a plan that left it out would number
    fifteen of sixteen positions. Its footprint is drawn here from the model -- its
    extent, through the same verified transform -- as a thin projection line, and
    described as ``projection``.

    It is written the way the serializer writes a product: a group carrying the
    GlobalId, the class and the name, inside the view group and in the view's own
    coordinates. An SVG reader then finds it exactly as it finds everything else.

    Args:
        facts: What the model says.
        built: Where the plan is cut.
        frame: The view's placement.
        plane: The view plane's origin and axes, in model units.
        drawn: The elements the serializer drew.
        placement: The view group's offset on the sheet, x and y, and the sheet's
            height, which together take paper millimetres into the group's units.

    Returns:
        One element per building element seen below the cut, and the SVG groups that
        draw them.
    """
    origin, x_axis, y_axis = plane
    offset_x, offset_y, height = placement
    seen = {element.ifc_guid for element in drawn}
    cut = built.storey_elevation + (built.cut_height or 0.0)
    added: list[Element] = []
    markup: list[str] = []
    for guid, (ifc_class, name) in sorted(facts.classes.items()):
        extent = facts.extents.get(guid)
        if guid in seen or extent is None:
            continue
        low, high = extent
        if not (built.storey_elevation - _CUT_TOLERANCE_M <= low[2] and high[2] < cut):
            continue
        corners = [(low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1])]
        outline: list[tuple[float, float]] = []
        for x, y in corners:
            point = (x / facts.unit_scale, y / facts.unit_scale, low[2] / facts.unit_scale)
            relative = [coordinate - base for coordinate, base in zip(point, origin, strict=True)]
            u = sum(a * b for a, b in zip(relative, x_axis, strict=True))
            v = sum(a * b for a, b in zip(relative, y_axis, strict=True))
            paper = invert(frame.transform, u, v)
            outline.append((round(paper[0], 3), round(paper[1], 3)))
        outline.append(outline[0])
        path = " ".join(
            f"{'M' if index == 0 else 'L'}{x - offset_x:.3f},{height - y - offset_y:.3f}"
            for index, (x, y) in enumerate(outline)
        )
        markup.append(
            f'<g id="product-{uuid_from_guid(guid)}-projection" class="{ifc_class}" '
            f'ifc:name="{escape(name or "")}" ifc:guid="{guid}">'
            f'<path d="{path}" fill="none" stroke="#000" stroke-width="0.18"/></g>'
        )
        added.append(
            Element(
                id=f"e-{len(drawn) + len(added):02d}",
                ifcGuid=guid,
                ifcClass=ifc_class,
                name=name,
                tag=facts.tags.get(guid),
                viewport=frame.viewport,
                paperBBox=bounding_box(outline),
                paperOutlines=[outline],
                representation="projection",
                properties=cast("dict[str, Any] | None", facts.properties.get(guid)),
                provenance=Provenance.AUTHORED,
            )
        )
    return added, "".join(markup)


def _reference(element: Element) -> tuple[str, str] | None:
    """Return the property path and value of an element's cross-section, if it has one.

    Args:
        element: The element.

    Returns:
        ``("Pset_ColumnCommon.Reference", "30/30")``, or None.
    """
    for name, values in sorted((element.properties or {}).items()):
        value = values.get("Reference") if isinstance(values, dict) else None
        if value:
            return f"{name}.Reference", str(value)
    return None


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _draw_grids(sheet: Sheet, grids: tuple[GridAxis, ...], frame: _Frame) -> list[Annotation]:
    """Draw the grid lines and their bubbles, and describe them.

    Args:
        sheet: The sheet being composed.
        grids: The grid lines.
        frame: The view's placement.

    Returns:
        One ``grid`` annotation per line.
    """
    annotations: list[Annotation] = []
    low_x, low_y, high_x, high_y = VIEWPORT_BOX
    for grid in grids:
        if grid.vertical:
            x = frame.paper_x(grid.position)
            start, end = (x, low_y + 4.0), (x, high_y - 4.0 - BUBBLE_OFFSET_MM)
            bubble = (x, end[1] + BUBBLE_RADIUS_MM)
        else:
            # Bubbled on the left: the right-hand side belongs to the dimensions.
            y = frame.paper_y(grid.position)
            start, end = (low_x + 4.0 + BUBBLE_OFFSET_MM, y), (high_x - 4.0, y)
            bubble = (start[0] - BUBBLE_RADIUS_MM, y)
        sheet.line(*start, *end, width=0.18)
        sheet.circle(*bubble, BUBBLE_RADIUS_MM)
        sheet.text(bubble[0], bubble[1] - 1.2, grid.axis, size=3.5, anchor="middle")
        annotations.append(
            Annotation(
                id=f"g-{grid.axis}",
                type="grid",
                viewport=frame.viewport,
                # The bubble and the line both belong to the grid, so the box has
                # to hold both: a box around the bubble alone leaves the line
                # outside it, which SPEC 3.4 forbids and PL-GEO-004 catches.
                paperBBox=bounding_box(
                    [
                        start,
                        end,
                        (bubble[0] - BUBBLE_RADIUS_MM, bubble[1] - BUBBLE_RADIUS_MM),
                        (bubble[0] + BUBBLE_RADIUS_MM, bubble[1] + BUBBLE_RADIUS_MM),
                    ]
                ),
                text=grid.axis,
                axis=grid.axis,
                geometry=[start, end],
                provenance=Provenance.AUTHORED,
            )
        )
    return annotations


def level_text(elevation_m: float) -> str:
    """Print an elevation as a German drawing does: ``±0,00``, ``+3,00``, ``-0,25``.

    Args:
        elevation_m: The elevation in metres.

    Returns:
        The level's text.
    """
    if abs(elevation_m) < 0.005:  # noqa: PLR2004 - below the printed precision
        return "±0,00"
    sign = "+" if elevation_m > 0 else "-"
    return f"{sign}{abs(elevation_m):.2f}".replace(".", ",")


def _draw_levels(
    sheet: Sheet, levels: Sequence[Level], frame: _Frame, *, left: float
) -> list[Annotation]:
    """Mark each storey's level beside a section, from the model's elevations.

    The mark's box is centred on the level line, so that the point it stands for is
    the height it states: a reader taking the box's centre through ``paperToPlane``
    lands on the elevation printed.

    Args:
        sheet: The sheet being composed.
        levels: The storeys to mark, lowest first.
        frame: The view's placement.
        left: The drawing's left edge on the paper.

    Returns:
        One ``level`` annotation per storey.
    """
    annotations: list[Annotation] = []
    right = left - 2.0
    start = right - LEVEL_LENGTH_MM
    for index, level in enumerate(levels):
        plane_y = level.elevation - frame.origin_z * frame.unit_scale
        y = frame.paper_y(plane_y)
        text = level_text(level.elevation)
        sheet.line(start, y, right, y, width=0.25)
        marker = start + 4.0
        sheet.line(marker - 1.5, y - 2.5, marker + 1.5, y - 2.5, width=0.18)
        sheet.line(marker - 1.5, y - 2.5, marker, y, width=0.18)
        sheet.line(marker + 1.5, y - 2.5, marker, y, width=0.18)
        sheet.text(marker + 3.0, y + 0.8, text, size=2.5)
        annotations.append(
            Annotation(
                id=f"lvl-{index}",
                type="level",
                viewport=frame.viewport,
                paperBBox=(
                    start,
                    y - LEVEL_HALF_HEIGHT_MM,
                    right,
                    y + LEVEL_HALF_HEIGHT_MM,
                ),
                text=text,
                elevation=level.elevation,
                ifcGuid=level.guid,
                geometry=[(start, y), (right, y)],
                provenance=Provenance.AUTHORED,
            )
        )
    return annotations


def _dimension(
    sheet: Sheet,
    identifier: str,
    start: tuple[float, float],
    end: tuple[float, float],
    value_mm: float,
    measures: tuple[str, str],
    viewport: str,
) -> Annotation:
    """Draw one dimension line with its value, and describe it.

    Args:
        sheet: The sheet being composed.
        identifier: The annotation's local id.
        start: One end on the paper.
        end: The other end.
        value_mm: The length it states, in millimetres, from the model.
        measures: The local ids of the two things it runs between.
        viewport: The viewport's local id.

    Returns:
        The ``dimension`` annotation.
    """
    sheet.line(*start, *end, width=0.18)
    for x, y in (start, end):
        sheet.line(x - 1.0, y - 1.0, x + 1.0, y + 1.0, width=0.25)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    printed = f"{value_mm:.0f}"
    width = len(printed) * _DIMENSION_CHAR_MM
    if start[0] == end[0]:
        # Up the page: the value reads beside the line rather than across it.
        sheet.text(midpoint[0] + 1.5, midpoint[1] - 1.0, printed, size=3.0)
        corner = (midpoint[0] + 1.5 + width, midpoint[1] + 2.0)
    else:
        sheet.text(midpoint[0], midpoint[1] + 1.5, printed, size=3.0, anchor="middle")
        corner = (midpoint[0] + width / 2.0, midpoint[1] + 4.0)
    return Annotation(
        id=identifier,
        type="dimension",
        viewport=viewport,
        paperBBox=bounding_box([start, end, corner]),
        text=printed,
        value=round(value_mm, 3),
        unit="mm",
        measures=list(measures),
        geometry=[start, end],
        provenance=Provenance.AUTHORED,
    )


def _draw_grid_dimensions(
    sheet: Sheet, grids: tuple[GridAxis, ...], frame: _Frame, content: Sequence[float]
) -> list[Annotation]:
    """Dimension each bay between consecutive grid lines, then the overall run.

    The value is the distance the model places between the two grids, in millimetres.
    It is not measured off the drawing, which is what makes it authored.

    Args:
        sheet: The sheet being composed.
        grids: The grid lines.
        frame: The view's placement.
        content: The drawing's box on the paper; the chains run just outside it.

    Returns:
        The ``dimension`` annotations, one chain per direction.
    """
    annotations: list[Annotation] = []
    for vertical in (True, False):
        line = sorted((g for g in grids if g.vertical is vertical), key=lambda g: g.position)
        pairs = list(pairwise(line))
        if len(line) > 2:  # noqa: PLR2004 - an overall dimension needs more than one bay
            pairs.append((line[0], line[-1]))
        for index, (first, second) in enumerate(pairs):
            outward = DIMENSION_OFFSETS_MM[0 if index < len(line) - 1 else 1]
            if vertical:
                y = content[1] - outward
                start = (frame.paper_x(first.position), y)
                end = (frame.paper_x(second.position), y)
            else:
                x = content[2] + outward
                start = (x, frame.paper_y(first.position))
                end = (x, frame.paper_y(second.position))
            annotations.append(
                _dimension(
                    sheet,
                    f"a-dim-{first.axis}{second.axis}",
                    start,
                    end,
                    abs(second.position - first.position) * 1000.0,
                    (f"g-{first.axis}", f"g-{second.axis}"),
                    frame.viewport,
                )
            )
    return annotations


def _draw_level_dimensions(
    sheet: Sheet, marks: list[Annotation], levels: Sequence[Level], content: Sequence[float]
) -> list[Annotation]:
    """Dimension each storey height between level marks, then the overall height.

    Args:
        sheet: The sheet being composed.
        marks: The level annotations, in the order of ``levels``.
        levels: The storeys they mark.
        content: The drawing's box on the paper; the chain runs to its right.

    Returns:
        The ``dimension`` annotations; none when there are fewer than two levels.
    """
    annotations: list[Annotation] = []
    pairs = list(pairwise(range(len(levels))))
    if len(levels) > 2:  # noqa: PLR2004 - an overall dimension needs more than one storey
        pairs.append((0, len(levels) - 1))
    for index, (low, high) in enumerate(pairs):
        outward = DIMENSION_OFFSETS_MM[0 if index < len(levels) - 1 else 1]
        x = content[2] + outward
        start = (x, (marks[low].paper_bbox[1] + marks[low].paper_bbox[3]) / 2.0)
        end = (x, (marks[high].paper_bbox[1] + marks[high].paper_bbox[3]) / 2.0)
        annotations.append(
            _dimension(
                sheet,
                f"a-dim-lvl-{low}{high}",
                start,
                end,
                (levels[high].elevation - levels[low].elevation) * 1000.0,
                (marks[low].local_id, marks[high].local_id),
                marks[low].viewport or "",
            )
        )
    return annotations


def _tag_box(x: float, y: float, *, with_reference: bool) -> tuple[float, float, float, float]:
    """Return the paper box a mark printed at a point takes up.

    Args:
        x: The mark's centre.
        y: Its baseline.
        with_reference: Whether a cross-section line prints below it.

    Returns:
        The box.
    """
    below = REFERENCE_DROP_MM + REFERENCE_HEIGHT_MM - TAG_BELOW_MM if with_reference else 0.0
    return (
        x - TAG_HALF_WIDTH_MM,
        y - TAG_BELOW_MM - below,
        x + TAG_HALF_WIDTH_MM,
        y + TAG_ABOVE_MM,
    )


def _overlaps(first: Sequence[float], second: Sequence[float]) -> bool:
    """Say whether two boxes overlap.

    Args:
        first: One box.
        second: The other.

    Returns:
        True when they share any area.
    """
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


#: A mark's distances from what it marks, tried nearest first.
_TAG_GAPS_MM = (2.0, 6.0, 10.0)

#: An element wider and taller than this share of the drawing is marked inside it: it
#: is a slab, and beside it is the edge of the sheet.
_LARGE_SHARE = 0.5


def _is_large(box: Sequence[float], content: Sequence[float]) -> bool:
    """Say whether an element fills most of the drawing, as a slab does.

    Args:
        box: The element's box.
        content: The drawing's box.

    Returns:
        True when it is wider and taller than :data:`_LARGE_SHARE` of the drawing.
    """
    return (box[2] - box[0]) > _LARGE_SHARE * (content[2] - content[0]) and (
        box[3] - box[1]
    ) > _LARGE_SHARE * (content[3] - content[1])


def _tag_point(
    element: Element,
    placed: list[tuple[float, float, float, float]],
    obstacles: list[Sequence[float]],
    content: Sequence[float],
    *,
    with_reference: bool,
) -> tuple[float, float]:
    """Choose where a mark prints: beside its element, clear of everything else.

    A mark printed on a cut wall is black on black. It goes beside the element instead
    -- above or below a long one, left or right of a tall one, diagonally off a small
    one -- on the side facing the middle of the drawing first, at every distance and
    along the element's length, before it tries the outside. An element that fills the
    drawing, a slab, is marked inside itself at a quarter point.

    Args:
        element: The element to mark.
        placed: The boxes of the marks already placed.
        obstacles: The boxes of the other elements and of the dimensions and levels.
        content: The drawing's box, whose middle decides which side is inside.
        with_reference: Whether a cross-section line prints below the mark.

    Returns:
        The mark's centre and baseline.
    """
    x0, y0, x1, y1 = element.paper_bbox
    width, height = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    extra = REFERENCE_DROP_MM + REFERENCE_HEIGHT_MM - TAG_BELOW_MM
    below = TAG_BELOW_MM + (extra if with_reference else 0.0)
    middle = ((content[0] + content[2]) / 2.0, (content[1] + content[3]) / 2.0)
    half = TAG_HALF_WIDTH_MM
    candidates: list[tuple[float, float]] = []
    if _is_large(element.paper_bbox, content):
        quarters = (0.25, 0.75)
        candidates += [(x0 + width * fx, y0 + height * fy) for fy in quarters for fx in quarters]
    if width < 2 * half and height < 2 * half:
        for gap in _TAG_GAPS_MM:
            above, under = y1 + gap + below, y0 - gap - TAG_ABOVE_MM
            right, left = x1 + gap + half, x0 - gap - half
            candidates += [(right, above), (right, under), (left, above), (left, under)]
    elif width >= height:
        inward = cy < middle[1]
        for upward in (inward, not inward):
            for gap in _TAG_GAPS_MM:
                y = y1 + gap + below if upward else y0 - gap - TAG_ABOVE_MM
                candidates += [(x, y) for x in (cx, x0 + width / 4, x0 + 3 * width / 4)]
    else:
        inward = cx < middle[0]
        for rightward in (inward, not inward):
            for gap in _TAG_GAPS_MM:
                x = x1 + gap + half if rightward else x0 - gap - half
                candidates += [(x, y) for y in (cy, y0 + height / 4, y0 + 3 * height / 4)]
    for x, y in candidates:
        box = _tag_box(x, y, with_reference=with_reference)
        if not any(_overlaps(box, other) for other in [*placed, *obstacles]):
            return x, y
    return candidates[0]


def _draw_tags(
    sheet: Sheet,
    elements: list[Element],
    viewport: str,
    content: Sequence[float],
    drawn: list[Annotation],
) -> list[Annotation]:
    """Mark each element with the model's ``Tag``, and a member with its cross-section.

    Args:
        sheet: The sheet being composed.
        elements: The elements to mark.
        viewport: The viewport's local id.
        content: The drawing's box on the paper.
        drawn: The annotations already on the sheet; marks keep clear of all but the
            grid lines, which cross everything.

    Returns:
        A ``tag`` annotation per marked element, and a ``text`` annotation showing the
        cross-section of each member that has one.
    """
    annotations: list[Annotation] = []
    placed: list[tuple[float, float, float, float]] = []
    for element in elements:
        if not element.tag:
            continue
        reference = _reference(element)
        obstacles: list[Sequence[float]] = [
            other.paper_bbox
            for other in elements
            if other is not element and not _is_large(other.paper_bbox, content)
        ]
        obstacles += [a.paper_bbox for a in drawn if a.annotation_type != "grid"]
        x, y = _tag_point(element, placed, obstacles, content, with_reference=reference is not None)
        placed.append(_tag_box(x, y, with_reference=reference is not None))
        sheet.text(x, y, element.tag, size=2.5, anchor="middle")
        annotations.append(
            Annotation(
                id=f"a-tag-{element.local_id}",
                type="tag",
                viewport=viewport,
                paperBBox=(
                    x - TAG_HALF_WIDTH_MM,
                    y - TAG_BELOW_MM,
                    x + TAG_HALF_WIDTH_MM,
                    y + TAG_ABOVE_MM,
                ),
                text=element.tag,
                shows=Shows(element=element.local_id, property="Tag"),
                provenance=Provenance.AUTHORED,
            )
        )
        if reference is None:
            continue
        path, value = reference
        baseline = y - REFERENCE_DROP_MM
        sheet.text(x, baseline, value, size=2.0, anchor="middle")
        annotations.append(
            Annotation(
                id=f"a-ref-{element.local_id}",
                type="text",
                viewport=viewport,
                paperBBox=(
                    x - TAG_HALF_WIDTH_MM,
                    baseline - TAG_BELOW_MM,
                    x + TAG_HALF_WIDTH_MM,
                    baseline + REFERENCE_HEIGHT_MM - TAG_BELOW_MM,
                ),
                text=value,
                shows=Shows(element=element.local_id, property=path),
                provenance=Provenance.AUTHORED,
            )
        )
    return annotations


def _draw_callout(sheet: Sheet, target_sheet: str, viewport: str) -> Annotation:
    """Draw a callout pointing at another sheet.

    Args:
        sheet: The sheet being composed.
        target_sheet: The sheet id it refers to.
        viewport: The viewport's local id.

    Returns:
        The ``callout`` annotation.
    """
    centre = (VIEWPORT_BOX[2] - 12.0, VIEWPORT_BOX[1] + 8.0)
    sheet.circle(*centre, 6.0)
    sheet.text(centre[0], centre[1] - 1.2, target_sheet, size=2.5, anchor="middle")
    return Annotation(
        id="a-call-01",
        type="callout",
        viewport=viewport,
        paperBBox=(centre[0] - 6.0, centre[1] - 6.0, centre[0] + 6.0, centre[1] + 6.0),
        text=target_sheet,
        target=Target(sheetId=target_sheet),
        provenance=Provenance.AUTHORED,
    )


def _draw_title_block(
    sheet: Sheet,
    width_mm: float,
    height_mm: float,
    sheet_id: str,
    title: str,
    scale_denominator: float,
) -> None:
    """Draw the title block.

    Args:
        sheet: The sheet being composed.
        width_mm: The page width.
        height_mm: The page height.
        sheet_id: The sheet number.
        title: The sheet title.
        scale_denominator: The drawing scale's denominator.
    """
    box = title_block_box(width_mm, height_mm)
    sheet.rect(box)
    sheet.text(box[0] + 4.0, box[1] + 46.0, "Wohnanlage Lindenhof", size=5.0)
    sheet.text(box[0] + 4.0, box[1] + 38.0, "Projekt 2024-118", size=3.0)
    sheet.text(box[0] + 4.0, box[1] + 26.0, title, size=4.0)
    sheet.text(box[0] + 4.0, box[1] + 8.0, sheet_id, size=6.0)
    sheet.text(box[2] - 4.0, box[1] + 8.0, f"M 1:{scale_denominator:.0f}", size=4.0, anchor="end")
    sheet.text(box[2] - 4.0, box[1] + 20.0, "Index A", size=3.0, anchor="end")


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------
def _ground_truth(label: PageLabel, sheet_id: str, grids: tuple[GridAxis, ...]) -> list[Question]:
    """Derive questions whose answers come from the model, not from the drawing.

    Every question but one kind is about what the sheet shows, so a reader of the
    page alone can in principle answer it; the label is meant to make that easier,
    not possible. The exception is the GlobalId of a marked element: only the label
    carries it, and those questions say so with ``requiresLabel``.

    Args:
        label: The page label.
        sheet_id: The sheet number.
        grids: The grid lines.

    Returns:
        One question per fact worth asking about, in a stable order.
    """
    elements = label.elements or []
    annotations = label.annotations or []
    drawn = {element.ifc_class for element in elements}
    questions: list[Question] = [
        {
            "sheet": sheet_id,
            "category": "count",
            "question": (
                f"How many {_PLURAL[ifc_class]} ({ifc_class}) are drawn on sheet {sheet_id}?"
            ),
            "answer": sum(1 for element in elements if element.ifc_class == ifc_class),
        }
        for ifc_class in _PLURAL
        if ifc_class in drawn or ifc_class == "IfcWall"
    ]
    questions += [
        {
            "sheet": sheet_id,
            "category": "sheet",
            "question": f"What is the drawing scale of sheet {sheet_id}? Answer as 1:n.",
            "answer": label.sheet.scale,
        },
        {
            "sheet": sheet_id,
            "category": "sheet",
            "question": (
                f"What kind of drawing is sheet {sheet_id}? Answer with one of: "
                f"{', '.join(_DRAWING_TYPES)}."
            ),
            "answer": label.sheet.drawing_type,
        },
    ]
    questions += _dimension_questions(annotations, sheet_id)
    questions += [
        {
            "sheet": sheet_id,
            "category": "callout",
            "question": f"Which sheet does the callout on {sheet_id} refer to?",
            "answer": annotation.target.sheet_id,
        }
        for annotation in annotations
        if annotation.annotation_type == "callout" and annotation.target is not None
    ]
    questions.append(
        {
            "sheet": sheet_id,
            "category": "grid",
            "question": f"Which grid axes are drawn on sheet {sheet_id}?",
            "answer": sorted(grid.axis for grid in grids),
        }
    )
    questions += _element_questions(elements, sheet_id)
    questions += [
        {
            "sheet": sheet_id,
            "category": "level",
            "question": (
                f"On sheet {sheet_id}, at what elevation in metres is the level marked "
                f"{annotation.text}?"
            ),
            "answer": annotation.elevation,
            "unit": "m",
        }
        for annotation in annotations
        if annotation.annotation_type == "level" and annotation.elevation is not None
    ]
    tagged = [element for element in elements if element.tag]
    if tagged:
        questions.append(
            {
                "sheet": sheet_id,
                "category": "model",
                "question": (
                    f"What is the IFC GlobalId of the element marked '{tagged[0].tag}' "
                    f"on sheet {sheet_id}?"
                ),
                "answer": tagged[0].ifc_guid,
                "requiresLabel": True,
            }
        )
    return questions


def _dimension_questions(annotations: list[Annotation], sheet_id: str) -> list[Question]:
    """Ask for each dimensioned distance: between two grids, or two levels.

    Args:
        annotations: The sheet's annotations.
        sheet_id: The sheet number.

    Returns:
        One question per dimension, in the order they were drawn.
    """
    by_id = {annotation.local_id: annotation for annotation in annotations}
    questions: list[Question] = []
    for annotation in annotations:
        if annotation.annotation_type != "dimension" or annotation.value is None:
            continue
        first, second = (by_id[end] for end in (annotation.measures or [])[:2])
        if first.annotation_type == "level":
            between = f"the levels {first.text} and {second.text}"
            what = "vertical distance"
        else:
            between = f"grid {first.axis} and grid {second.axis}"
            what = "distance"
        questions.append(
            {
                "sheet": sheet_id,
                "category": "dimension",
                "question": (
                    f"On sheet {sheet_id}, what is the {what} in millimetres between {between}?"
                ),
                "answer": annotation.value,
                "unit": "mm",
            }
        )
    return questions


def _element_questions(elements: list[Element], sheet_id: str) -> list[Question]:
    """Ask what a mark names, and what cross-section a marked member has.

    One question per IFC class drawn, about the first element of that class, so a
    sheet of sixteen positions does not become sixteen near-identical questions.

    Args:
        elements: The sheet's elements.
        sheet_id: The sheet number.

    Returns:
        The ``tag`` and ``section`` questions.
    """
    questions: list[Question] = []
    first_of_class: dict[str, Element] = {}
    for element in elements:
        if element.tag:
            first_of_class.setdefault(element.ifc_class, element)
    questions += [
        {
            "sheet": sheet_id,
            "category": "tag",
            "question": (
                f"On sheet {sheet_id}, what kind of element carries the mark "
                f"'{element.tag}'? Answer with its IFC class, such as IfcWall."
            ),
            "answer": element.ifc_class,
        }
        for element in first_of_class.values()
    ]
    for element in first_of_class.values():
        reference = _reference(element)
        if reference is None:
            continue
        thickness = re.fullmatch(r"d\s*=\s*(\d+(?:[.,]\d+)?)\s*cm", reference[1])
        if thickness is not None:
            questions.append(
                {
                    "sheet": sheet_id,
                    "category": "section",
                    "question": (
                        f"On sheet {sheet_id}, how thick in centimetres is the member "
                        f"marked '{element.tag}'?"
                    ),
                    "answer": float(thickness.group(1).replace(",", ".")),
                    "unit": "cm",
                }
            )
        else:
            questions.append(
                {
                    "sheet": sheet_id,
                    "category": "section",
                    "question": (
                        f"On sheet {sheet_id}, what cross-section does the member marked "
                        f"'{element.tag}' have? Answer as width/depth in centimetres, "
                        "such as 24/24."
                    ),
                    "answer": reference[1],
                }
            )
    return questions


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def write_sample(
    exported: ExportedSheet,
    built: BuiltModel,
    out_dir: Path,
    *,
    mod_date: datetime,
    inkscape_fallback: bool = False,
) -> Path:
    """Write one complete sample set.

    Args:
        exported: The composed sheet and its label.
        built: The model it was drawn from.
        out_dir: The directory to write into, which is created.
        mod_date: The timestamp to stamp, so the output is reproducible.
        inkscape_fallback: Convert with Inkscape when CairoSVG cannot run.

    Returns:
        The labelled PDF's path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sheet.svg").write_text(exported.svg, encoding="utf-8")
    labels_path = out_dir / "labels.json"
    labels_path.write_text(canonical_json(exported.label), encoding="utf-8")

    width = exported.label.page.width_mm
    height = exported.label.page.height_mm
    plain = svg_to_pdf(
        exported.svg,
        out_dir / "sheet.pdf",
        width_mm=width,
        height_mm=height,
        mod_date=mod_date,
        inkscape_fallback=inkscape_fallback,
    )
    labelled = out_dir / "sheet.labelled.pdf"
    index = embed.build_index([exported.label])
    embed.attach(plain, [exported.label], index, labelled, mod_date=mod_date)

    (out_dir / "groundtruth.jsonl").write_text(
        "".join(
            json.dumps(question, ensure_ascii=False, sort_keys=True) + "\n"
            for question in exported.ground_truth
        ),
        encoding="utf-8",
    )
    if built.path.resolve() != (out_dir / "model.ifc").resolve():
        (out_dir / "model.ifc").write_bytes(built.path.read_bytes())
    return labelled
