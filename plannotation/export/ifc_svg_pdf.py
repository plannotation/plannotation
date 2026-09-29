# SPDX-License-Identifier: Apache-2.0
"""The authored exporter: a model in, a plannotated drawing out.

Every value this writes is computed from the model, which is what makes the output
``authored`` rather than ``inferred`` (SPEC 4.6). A dimension's value is the distance
between two grid lines, or two storeys, as the model places them, not a number
measured off a picture; an element's ``paperBBox`` is its drawn geometry through the
verified paper transform; whether it is cut or seen beyond the cut comes from its
geometry in the model; a tag prints the ``Tag`` the model holds, and a member's
cross-section is the ``Reference`` of its common property set. Nothing here reads the
drawing to find out what the drawing says.

The model's length unit is read from the model, never assumed: ``model.lengthUnit``,
``paperToPlane``, ``plane`` and ``cutHeight`` are all in it (SPEC 3.5), and a
plannotation that named one unit while the file used another would put every coordinate
a factor of a thousand out.

The sheet is composed rather than rendered whole: the serializer draws the building --
a plan cut, or a vertical section -- and this module puts a frame, a title block, grid
lines and bubbles, level marks, dimensions, tags and a callout around it. Those are
the parts a plannotation has something to say about, and a sample without them would
exercise nothing beyond L2.
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from html import escape
from itertools import pairwise
from typing import TYPE_CHECKING, Any, cast, get_args

import numpy as np

from plannotation.constants import SCHEMA_VERSION
from plannotation.errors import ExportError
from plannotation.export.doors import door_swings
from plannotation.export.drafting import (
    GRID_DASH,
    LEVEL_TRIANGLE_MM,
    MARK_RADIUS_MM,
    NORTH_RADIUS_MM,
    Line,
    Point,
    RoomLabels,
    SectionMark,
    ViewTitle,
    area_text,
    draw_label,
    draw_level_mark,
    draw_north_arrow,
    draw_scale_bar,
    draw_section_mark,
    draw_view_title,
    place_label,
    present,
    segments_of,
    style_of,
)
from plannotation.export.geometry import bounding_box, path_points, union_box
from plannotation.export.paper import (
    Affine,
    apply,
    invert,
    paper_to_plane,
    plane_from_ifc_plane,
)
from plannotation.export.sheet import PAPER_SIZES, Sheet, frame_box, text_width, title_block_box
from plannotation.export.svg_render import RenderedView, render_view
from plannotation.export.to_pdf import svg_to_pdf
from plannotation.model import (
    Annotation,
    DrawingType,
    Element,
    Generator,
    Model,
    Page,
    Plane,
    Plannotation,
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
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from numpy.typing import NDArray

    from plannotation.export.models import BuiltModel, Level, SectionCut
    from plannotation.model import Discipline

#: A paper bounding box, ``(x0, y0, x1, y1)`` in millimetres.
Box = tuple[float, float, float, float]

#: Where the drawing sits on an A3 sheet: :func:`viewport_box` for that page.
VIEWPORT_BOX: Box = (25.0, 85.0, 325.0, 285.0)

#: The viewport's clearance inside the frame on the left and above the title block,
#: the room kept on its right for the dimension chains, and its clearance at the top.
_VIEWPORT_INSET_MM, _VIEWPORT_RIGHT_MM, _VIEWPORT_TOP_MM = 15.0, 85.0, 2.0

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
class Wording:
    """The words a compact title block prints around its values, in the sheet's language.

    Attributes:
        project: Printed before the project number.
        revision: Printed before the revision.
        scale: The scale as printed, ``{denominator}`` standing for its denominator.
    """

    project: str = "Project"
    revision: str = "Revision"
    scale: str = "1:{denominator}"


@dataclass(frozen=True)
class TitleField:
    """One labelled field of a title block, such as ``SCALE`` and ``1:100``.

    Attributes:
        label: The field's label as it prints, in as many languages as the sheet uses.
        value: Its value. A long value wraps onto further lines.
    """

    label: str
    value: str


@dataclass(frozen=True)
class SheetSpec:
    """What is drawn on one sheet, and what the sheet says about itself.

    Nothing about the project is assumed: a value the spec leaves out is read from the
    model where the model holds it, and otherwise neither printed nor recorded.

    Attributes:
        sheet_id: The sheet number as it prints.
        title: The sheet title as it prints.
        scale: The drawing scale's denominator.
        grids: The grid lines to draw and dimension between.
        callout_to: The sheet the callout in the viewport's corner points at, or None
            for no such callout.
        drawing_type: What kind of drawing this is. It is stated rather than assumed:
            a plannotation calling a position plan an architectural plan makes a false
            statement about the sheet, and it is exactly the one inference caught.
        discipline: The discipline the drawing belongs to.
        project: The project's name and number; None takes the name from the model's
            ``IfcProject``.
        revision: The sheet's revision, or None for a sheet that has none.
        page_size: A key of :data:`plannotation.export.sheet.PAPER_SIZES`.
        viewport_box: Where the view is drawn on the paper; None for
            :func:`viewport_box` of the page.
        wording: The words a compact title block prints.
        title_fields: The title block's labelled fields. With none, the title block is
            the compact one: project, title, sheet number, scale and revision.
        title_block_mm: The title block's width and height; None for
            :data:`plannotation.export.sheet.TITLE_BLOCK_MM`.
        presentation: Draw as an architect would: cut structure solid, partitions grey,
            everything else cut in outline, spaces unfilled, what lies beyond the cut
            in fine lines, grids as chain lines. Without it the serializer's paths keep
            SVG's default, filled black, which is what the samples were drawn with.
        material_styles: How a cut part -- one layer of a roof or a wall -- is drawn,
            by what it is made of: pairs of a regular expression, searched for in a
            material's name, and a key of :data:`plannotation.export.drafting.STYLES`.
            A part takes the style of the first expression any of its materials
            matches; one whose materials match none is drawn as its class says, and
            one with no material as the element it is part of.
        grid_overshoot_mm: How far grid lines run beyond the drawing, with the bubble
            beyond that; None to run them across the whole viewport.
        dimension_offsets_mm: How far from the drawing the two dimension chains run.
        rooms: How to label the rooms, or None for no room labels.
        door_swings: Draw each swinging door open with the arc of its leaf.
        section_marks: The sections whose cutting lines a plan marks.
        view_title: The title under the view, or None for none.
        north_arrow: Draw a north arrow turned to the model's true north.
        scale_bar: Draw a scale bar beside the view title.
        psets: Property sets to read besides the common ones; the room labels' area
            property set is read whatever this says.
    """

    sheet_id: str
    title: str
    scale: float
    grids: tuple[GridAxis, ...]
    callout_to: str | None
    drawing_type: DrawingType = "plan"
    discipline: Discipline = "architecture"
    project: Project | None = None
    revision: str | None = None
    page_size: str = "A3"
    viewport_box: Box | None = None
    wording: Wording = field(default_factory=Wording)
    title_fields: tuple[TitleField, ...] = ()
    title_block_mm: tuple[float, float] | None = None
    presentation: bool = False
    material_styles: tuple[tuple[str, str], ...] = ()
    grid_overshoot_mm: float | None = None
    dimension_offsets_mm: tuple[float, float] = (10.0, 22.0)
    rooms: RoomLabels | None = None
    door_swings: bool = False
    section_marks: tuple[SectionMark, ...] = ()
    view_title: ViewTitle | None = None
    north_arrow: bool = False
    scale_bar: bool = False
    psets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportedSheet:
    """Everything one exported sheet produced.

    Attributes:
        svg: The composed sheet.
        plannotation: The plannotation describing it.
        ground_truth: Questions with answers taken from the model.
    """

    svg: str
    plannotation: Plannotation
    ground_truth: tuple[Question, ...]


@dataclass(frozen=True)
class ModelFacts:
    """What the exporter reads from the model rather than from the drawing.

    Attributes:
        unit_scale: The model's length unit in metres.
        sha256: The SHA-256 of the model file's bytes, which ``model.sha256`` records.
        schema: The model's IFC schema, such as ``IFC4`` or ``IFC2X3``.
        project: The ``IfcProject``'s name, or None.
        classes: Each building element's IFC class and name, by GlobalId.
        tags: Each product's ``Tag``, by GlobalId, where it is a mark: a tag shaped like
            a GUID is an authoring tool's internal id, not a mark anyone reads.
        properties: Each product's common property sets, and any other property set
            asked for, by GlobalId.
        extents: Each product's world bounding box in metres, by GlobalId.
        depths: Where a cutting plane was given, how far each product reaches along its
            normal, least and most, in metres from the plane: a product is cut when the
            two straddle zero.
        edges: Where a cutting plane was given, each product's edges beyond it, as
            :func:`edges_beyond` returns them: the lines a drawing of what lies beyond
            the cut could show of it.
        north: True north in model x and y.
        names: Each space's ``Name`` and ``LongName``, by GlobalId.
        materials: Each product's materials' names, by GlobalId, in the order the model
            gives them: its material, or the materials of its layer set, list, profile
            set or constituent set, taken from its type where it has none of its own.
        aggregates: For each product that is part of another, by GlobalId, the other's
            IFC class and the ``LoadBearing`` its common property set states, or None
            where it states none.
    """

    unit_scale: float
    sha256: str
    schema: str = "IFC4"
    project: str | None = None
    classes: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    properties: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    extents: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = field(default_factory=dict)
    depths: dict[str, tuple[float, float]] = field(default_factory=dict)
    edges: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    north: tuple[float, float] = (0.0, 1.0)
    names: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    materials: dict[str, tuple[str, ...]] = field(default_factory=dict)
    aggregates: dict[str, tuple[str, bool | None]] = field(default_factory=dict)

    def is_a(self, ifc_class: str, ancestor: str) -> bool:
        """Say whether a class is another, or a subtype of it, in this model's schema.

        Args:
            ifc_class: The class.
            ancestor: The class it may be, or descend from.

        Returns:
            True when an ``ifc_class`` is an ``ancestor``.
        """
        return is_subtype(self.schema, ifc_class, ancestor)


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

    def paper(self, plane_x_m: float, plane_y_m: float) -> tuple[float, float]:
        """Return the paper point of a plane point, given in metres.

        Args:
            plane_x_m: The plane x in metres.
            plane_y_m: The plane y in metres.

        Returns:
            The paper point in millimetres, rounded as the format serialises it.
        """
        x, y = invert(self.transform, plane_x_m / self.unit_scale, plane_y_m / self.unit_scale)
        return (round(x, 3), round(y, 3))


def viewport_box(
    width_mm: float, height_mm: float, title_block_mm: tuple[float, float] | None = None
) -> Box:
    """Return where a view is drawn on a page of a given size, by default.

    Inside the frame, above the title block, with room on the right for the dimension
    chains. On an A3 page with the default title block this is :data:`VIEWPORT_BOX`.

    Args:
        width_mm: The page width.
        height_mm: The page height.
        title_block_mm: The title block's size, or None for the default.

    Returns:
        The viewport's paper box.
    """
    left, _, right, top = frame_box(width_mm, height_mm)
    block = title_block_box(width_mm, height_mm, title_block_mm)
    return (
        left + _VIEWPORT_INSET_MM,
        block[3] + _VIEWPORT_INSET_MM,
        right - _VIEWPORT_RIGHT_MM,
        top - _VIEWPORT_TOP_MM,
    )


def export_sheet(
    built: BuiltModel,
    spec: SheetSpec,
    *,
    generator_version: str,
) -> ExportedSheet:
    """Draw one model on one sheet and describe it.

    Args:
        built: The model to draw.
        spec: What the sheet is and what is drawn on it.
        generator_version: The version to record in ``generator``.

    Returns:
        The composed sheet, its plannotation, and its ground truth.

    Raises:
        ExportError: If the view draws no element.
    """
    width_mm, height_mm = PAPER_SIZES[spec.page_size]
    box = spec.viewport_box or viewport_box(width_mm, height_mm, spec.title_block_mm)
    plan = built.is_plan
    psets = (*spec.psets, *_area_pset(spec.rooms))
    facts = read_model_facts(built.path, include=built.include, plane=built.section, psets=psets)
    view = render_view(
        built.path,
        scale_denominator=spec.scale,
        width_mm=box[2] - box[0],
        height_mm=box[3] - box[1],
        # The serializer cuts at an absolute z, so the storey's own z goes in with it.
        section_height=built.storey_elevation + (built.cut_height or 0.0),
        section=built.section,
        name=spec.title,
        include=built.include,
    )

    sheet = Sheet(width_mm=width_mm, height_mm=height_mm)
    sheet.rect(frame_box(width_mm, height_mm))
    # The serializer draws into its own box, whose top-left goes at the viewport's
    # top-left on the sheet. In SVG that is a downward y, so the paper top edge.
    offset = (box[0], sheet.y(box[3]))

    transform = paper_to_plane(view.matrix3, height_mm, facts.unit_scale, svg_offset=offset)
    origin, x_axis, y_axis = plane_from_ifc_plane(view.ifc_plane, facts.unit_scale)
    frame = _Frame(
        viewport="vp-plan" if plan else "vp-section",
        transform=transform,
        unit_scale=facts.unit_scale,
        origin_z=origin[2],
    )

    plane = (origin, x_axis, y_axis)
    seen_lines = _seen_lines(view.svg, height_mm, offset)
    elements, group = _draw_view(
        view, spec, built, facts, frame, plane, (*offset, height_mm), seen_lines
    )
    sheet.group(group, transform=f"translate({offset[0]},{offset[1]})")
    if not elements:
        msg = f"sheet {spec.sheet_id} drew no elements; the view would be empty"
        raise ExportError(msg)
    annotations = _annotate(
        sheet, spec, built, facts, frame, box, elements, plane=plane, seen_lines=seen_lines
    )
    project = spec.project or (Project(name=facts.project) if facts.project else None)
    _draw_title_block(sheet, spec, project)

    boxes = [element.paper_bbox for element in elements]
    boxes += [a.paper_bbox for a in annotations if a.viewport == frame.viewport]
    viewport = _viewport(built, spec, frame, facts.unit_scale, plane, boxes)
    plannotation = Plannotation(
        plannotation=SCHEMA_VERSION,
        generator=Generator(name="plannotation", version=generator_version),
        provenance=Provenance.AUTHORED,
        page=Page(index=0, widthMm=width_mm, heightMm=height_mm),
        sheet=SheetInfo(
            id=spec.sheet_id,
            title=spec.title,
            revision=spec.revision,
            discipline=spec.discipline,
            drawingType=spec.drawing_type,
            scale=spec.scale,
            project=project,
            titleBlockBBox=title_block_box(width_mm, height_mm, spec.title_block_mm),
        ),
        model=Model(
            file=built.path.name,
            sha256=facts.sha256,
            lengthUnit=length_unit_for(facts.unit_scale),
            schema=facts.schema,
        ),
        viewports=[viewport],
        elements=elements,
        annotations=annotations,
    )
    return ExportedSheet(
        svg=sheet.render(),
        plannotation=plannotation,
        ground_truth=tuple(_ground_truth(plannotation, spec.sheet_id, spec.grids, facts.is_a)),
    )


def _draw_view(
    view: RenderedView,
    spec: SheetSpec,
    built: BuiltModel,
    facts: ModelFacts,
    frame: _Frame,
    plane: tuple[list[float], list[float], list[float]],
    placement: tuple[float, float, float],
    seen_lines: list[list[Point]],
) -> tuple[list[Element], str]:
    """Describe what the serializer drew, add what it leaves out, and style it.

    Args:
        view: The rendered view.
        spec: The sheet.
        built: The model and where it is cut.
        facts: What the model says.
        frame: The view's placement.
        plane: The plane's origin and axes, in model units.
        placement: The view group's offset on the sheet, x and y, and the sheet's height.
        seen_lines: The lines the serializer draws beyond the cut, on the paper.

    Returns:
        The elements, and the view group to place on the sheet.
    """
    offset, height = (placement[0], placement[1]), placement[2]
    elements = _elements(view, height, offset, facts, built, frame.viewport)
    group = _view_group(view.svg)
    if built.section is None:
        seen, markup = _projections(facts, built, frame, plane, elements, placement)
        elements += seen
        group = group[: -len("</g>")] + markup + "</g>"
    else:
        elements += _seen_beyond(facts, built, frame, elements, seen_lines)
    if spec.door_swings and built.is_plan:
        group = _draw_swings(group, elements, built, frame, placement)
    if spec.presentation:
        group = present(group, _styles(elements, facts, spec.material_styles))
    return elements, group


def _annotate(  # noqa: PLR0913 - every annotation needs some of these
    sheet: Sheet,
    spec: SheetSpec,
    built: BuiltModel,
    facts: ModelFacts,
    frame: _Frame,
    box: Box,
    elements: list[Element],
    *,
    plane: tuple[list[float], list[float], list[float]],
    seen_lines: list[list[Point]],
) -> list[Annotation]:
    """Draw every annotation around and in the view, in the order they must avoid each other.

    Args:
        sheet: The sheet being composed.
        spec: The sheet.
        built: The model and where it is cut.
        facts: What the model says.
        frame: The view's placement.
        box: The viewport's paper box.
        elements: The elements drawn.
        plane: The plane's origin and axes.
        seen_lines: The lines drawn beyond the cut.

    Returns:
        The annotations.
    """
    content = union_box(element.paper_bbox for element in elements)
    annotations: list[Annotation] = []
    annotations += _draw_grids(
        sheet,
        spec.grids,
        frame,
        box,
        content=content if spec.grid_overshoot_mm is not None else None,
        overshoot=spec.grid_overshoot_mm or 0.0,
        dash=GRID_DASH if spec.presentation else None,
    )
    levels = _draw_levels(
        sheet,
        built.levels,
        frame,
        left=content[0],
        datum=built.datum,
        standing=spec.presentation,
    )
    annotations += levels
    offsets = spec.dimension_offsets_mm
    annotations += _draw_grid_dimensions(sheet, spec.grids, frame, content, offsets)
    annotations += _draw_level_dimensions(sheet, levels, built.levels, content, offsets)
    if spec.callout_to is not None:
        annotations.append(_draw_callout(sheet, spec.callout_to, frame.viewport, box))
    annotations += _draw_section_marks(sheet, spec.section_marks, frame, elements)
    if spec.rooms is not None:
        floor = (
            (round(built.storey_elevation - built.datum, 6), built.storey_guid)
            if built.is_plan and spec.presentation
            else None
        )
        annotations += _draw_rooms(
            sheet, spec.rooms, elements, annotations, (seen_lines, facts), frame, floor=floor
        )
    annotations += _draw_tags(sheet, elements, frame.viewport, content, annotations)
    axes = (plane[1], plane[2]) if built.is_plan else None
    annotations += _draw_view_furniture(sheet, spec, facts, frame, content, axes=axes)
    return annotations


def _viewport(
    built: BuiltModel,
    spec: SheetSpec,
    frame: _Frame,
    unit_scale: float,
    plane: tuple[list[float], list[float], list[float]],
    boxes: Sequence[Sequence[float]],
) -> Viewport:
    """Describe the view: where it sits, what plane it shows, and at what scale.

    Args:
        built: The model and where it is cut.
        spec: The sheet.
        frame: The view's placement.
        unit_scale: The model's length unit in metres.
        plane: The plane's origin and axes, in model units.
        boxes: The boxes of everything that belongs to the view.

    Returns:
        The viewport.
    """
    origin, x_axis, y_axis = plane
    plan = built.is_plan
    return Viewport(
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
        paperToPlane=frame.transform,
        cutHeight=(built.cut_height or 0.0) / unit_scale if plan else None,
        storey=(
            Storey(
                ifcGuid=built.storey_guid,
                name=built.storey_name,
                elevation=built.storey_elevation / unit_scale,
            )
            if plan
            else None
        ),
    )


def _area_pset(rooms: RoomLabels | None) -> tuple[str, ...]:
    """Return the property set a room label's area is read from, if it has one.

    Args:
        rooms: How rooms are labelled.

    Returns:
        The property set's name, alone, or nothing.
    """
    if rooms is None or rooms.area_property is None or "." not in rooms.area_property:
        return ()
    return (rooms.area_property.split(".", 1)[0],)


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
#: A ``Tag`` shaped like a GUID -- ``3F2504E0-4F89-11D3-9A0C-0305E82C3301`` or an IFC
#: GlobalId -- which authoring tools write there when nobody gave the element a mark.
_GUID_TAG = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
    r"|[0-3][0-9A-Za-z_$]{21}"
)


def open_ifc(path: Path) -> Any:  # noqa: ANN401 - ifcopenshell is untyped here
    """Open an IFC file.

    Args:
        path: The file.

    Returns:
        The ``ifcopenshell.file``.
    """
    return importlib.import_module("ifcopenshell").open(str(path))


def is_mark(tag: str, guid: str) -> bool:
    """Say whether a ``Tag`` is a mark a drawing prints, rather than an internal id.

    Archicad fills every element's ``Tag`` with its own GUID. Printed beside each wall,
    that is noise; asked about, it is a question nobody could answer from the sheet.

    Args:
        tag: The ``Tag``.
        guid: The element's GlobalId.

    Returns:
        False for a tag that is the GlobalId or shaped like a GUID.
    """
    stripped = tag.strip()
    return bool(stripped) and stripped != guid and _GUID_TAG.fullmatch(stripped) is None


@functools.cache
def is_subtype(schema: str, ifc_class: str, ancestor: str) -> bool:
    """Say whether a class is another, or a subtype of it, in a schema.

    Args:
        schema: The schema's name, such as ``IFC2X3``.
        ifc_class: The class.
        ancestor: The class it may be, or descend from.

    Returns:
        True when it is; a class the schema does not know is only itself.
    """
    if ifc_class.lower() == ancestor.lower():
        return True
    wrapper = importlib.import_module("ifcopenshell.ifcopenshell_wrapper")
    try:
        declaration = wrapper.schema_by_name(schema).declaration_by_name(ifc_class)
    except (RuntimeError, IndexError):
        return False
    while declaration is not None:
        if declaration.name().lower() == ancestor.lower():
            return True
        declaration = declaration.supertype()
    return False


def read_model_facts(
    model_path: Path,
    *,
    include: Sequence[str] | None = None,
    plane: SectionCut | None = None,
    psets: Sequence[str] = (),
) -> ModelFacts:
    """Read the unit, hash, marks, property sets, materials and extents out of a model.

    Args:
        model_path: The IFC file.
        include: The products whose geometry is measured; None for all of them.
        plane: A cutting plane to measure each product against: how far it reaches
            along the plane's normal, and its edges beyond the plane.
        psets: Property sets to read besides the common ones, such as a model's own
            room data.

    Returns:
        The facts.
    """
    ifcopenshell = importlib.import_module("ifcopenshell")
    unit = importlib.import_module("ifcopenshell.util.unit")
    util_element = importlib.import_module("ifcopenshell.util.element")
    model = ifcopenshell.open(str(model_path))
    tags: dict[str, str] = {}
    properties: dict[str, dict[str, dict[str, object]]] = {}
    materials: dict[str, tuple[str, ...]] = {}
    wholes: dict[str, Any] = {}
    classes = {
        str(element.GlobalId): (str(element.is_a()), element.Name or None)
        for element in model.by_type("IfcElement")
        if not element.is_a("IfcFeatureElementSubtraction")
    }
    wanted = set(psets)
    for product in model.by_type("IfcProduct"):
        guid = str(product.GlobalId)
        tag = getattr(product, "Tag", None)
        if tag and is_mark(str(tag), guid):
            tags[guid] = str(tag)
        chosen = {
            name: {key: value for key, value in values.items() if key != "id"}
            for name, values in util_element.get_psets(product).items()
            if (name.startswith("Pset_") and name.endswith("Common")) or name in wanted
        }
        if chosen:
            properties[guid] = chosen
        names = _material_names(product)
        if names:
            materials[guid] = names
        whole = util_element.get_aggregate(product)
        if whole is not None:
            wholes[guid] = whole
    projects = model.by_type("IfcProject")
    extents, depths, edges = _extents(model, include, plane)
    return ModelFacts(
        unit_scale=float(unit.calculate_unit_scale(model)),
        sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        schema=str(model.schema),
        project=str(projects[0].Name) if projects and projects[0].Name else None,
        classes=classes,
        tags=tags,
        properties=properties,
        extents=extents,
        depths=depths,
        edges=edges,
        north=_true_north(model),
        names={
            str(space.GlobalId): (space.Name or None, space.LongName or None)
            for space in model.by_type("IfcSpace")
        },
        materials=materials,
        aggregates={
            guid: (str(whole.is_a()), _load_bearing(properties.get(str(whole.GlobalId), {})))
            for guid, whole in wholes.items()
        },
    )


#: The members of a material set, each of which names one material.
_SET_MEMBERS = ("IfcMaterialLayer", "IfcMaterialProfile", "IfcMaterialConstituent")


def _material_names(product: Any) -> tuple[str, ...]:  # noqa: ANN401 - ifcopenshell is untyped
    """Return the names of a product's materials, in the order the model gives them.

    Args:
        product: The entity.

    Returns:
        The names of its material, or of its set's materials, taken from its type where
        it has none of its own; empty for a product with no material.
    """
    util_element = importlib.import_module("ifcopenshell.util.element")
    material = util_element.get_material(product, should_skip_usage=True)
    if material is None:
        return ()
    # get_materials reads a single material and every kind of set, but not one layer,
    # profile or constituent associated on its own, which the schema allows.
    if any(material.is_a(name) for name in _SET_MEMBERS):
        found = [material.Material]
    else:
        found = util_element.get_materials(product)
    return tuple(str(each.Name) for each in found if each is not None and each.Name)


def _load_bearing(psets: Mapping[str, object]) -> bool | None:
    """Return the ``LoadBearing`` a product's common property set states.

    Args:
        psets: Its property sets by name, as :attr:`ModelFacts.properties` holds them.

    Returns:
        True or False as a common property set states it; None where none does.
    """
    bearing = None
    for name, values in psets.items():
        if name.endswith("Common") and isinstance(values, dict):
            value = values.get("LoadBearing")
            if isinstance(value, bool):
                bearing = value
    return bearing


def _true_north(model: Any) -> tuple[float, float]:  # noqa: ANN401 - ifcopenshell is untyped
    """Return true north in model x and y.

    Args:
        model: The open ``ifcopenshell.file``.

    Returns:
        The model context's ``TrueNorth``, as a unit vector, or +y where none is stated.
    """
    for context in model.by_type("IfcGeometricRepresentationContext"):
        north = getattr(context, "TrueNorth", None)
        if north is not None and not context.is_a("IfcGeometricRepresentationSubContext"):
            x, y = (float(value) for value in north.DirectionRatios[:2])
            length = (x * x + y * y) ** 0.5
            return (x / length, y / length)
    return (0.0, 1.0)


def _extents(
    model: Any,  # noqa: ANN401
    include: Sequence[str] | None = None,
    plane: SectionCut | None = None,
) -> tuple[
    dict[str, tuple[tuple[float, ...], tuple[float, ...]]],
    dict[str, tuple[float, float]],
    dict[str, NDArray[np.float64]],
]:
    """Measure every product: its world box, and against a plane, what the view sees of it.

    Args:
        model: The open ``ifcopenshell.file``.
        include: The products to measure, or None for all of them.
        plane: The cutting plane, or None.

    Returns:
        World boxes in metres by GlobalId, then -- where a plane was given -- each
        product's least and most distance along the plane's normal, and its edges beyond
        the plane (:func:`edges_beyond`).
    """
    geom = importlib.import_module("ifcopenshell.geom")
    settings = geom.settings()
    settings.set("use-world-coords", True)  # noqa: FBT003 - the wrapper is positional
    if include is None:
        iterator = geom.iterator(settings, model, exclude=["IfcOpeningElement"])
    else:
        iterator = geom.iterator(settings, model, include=[model.by_guid(guid) for guid in include])
    extents: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
    depths: dict[str, tuple[float, float]] = {}
    edges: dict[str, NDArray[np.float64]] = {}
    frame = None
    if plane is not None:
        x_axis, y_axis = plane.axes()
        normal = np.cross(x_axis, y_axis)
        frame = (np.array(plane.location), np.array([x_axis, y_axis, normal]).T)
    if iterator.initialize():
        while True:
            shape = iterator.get()
            points = np.array(shape.geometry.verts, dtype=np.float64).reshape(-1, 3)
            if len(points):
                guid = str(shape.guid)
                low, high = points.min(axis=0), points.max(axis=0)
                extents[guid] = (tuple(map(float, low)), tuple(map(float, high)))
                if frame is not None:
                    local = (points - frame[0]) @ frame[1]
                    depths[guid] = (float(local[:, 2].min()), float(local[:, 2].max()))
                    faces = np.array(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
                    edges[guid] = edges_beyond(local, faces)
            if not iterator.next():
                break
    return extents, depths, edges


#: How finely an edge's ends are matched, in metres: a tenth of a millimetre.
_WELD_M = 1e-4


#: How far apart two faces' unit normals may be and still count as one flat face.
_FLAT = 1e-6


def edges_beyond(local: NDArray[np.float64], faces: NDArray[np.int64]) -> NDArray[np.float64]:
    """Return the edges of a shape that a view could draw beyond its cutting plane.

    An edge is where two faces meet at an angle, or where a face ends; a diagonal that
    splits a flat face into triangles is not one. Each edge is clipped to the far side of
    the plane, so that the part of a cut wall in front of the cut, which the view removes,
    draws nothing.

    Args:
        local: The shape's vertices in plane coordinates, the third being the distance
            towards the viewer.
        faces: Its triangles, as vertex indices.

    Returns:
        One row per edge, ``x0, y0, d0, x1, y1, d1``: its two ends in plane x and y,
        each with its distance towards the viewer, which is at most zero.
    """
    empty = np.zeros((0, 6), dtype=np.float64)
    if not len(faces):
        return empty
    keys = np.round(local / _WELD_M).astype(np.int64)
    welded, index = np.unique(keys, axis=0, return_inverse=True)
    triangles = index.reshape(-1)[faces]
    corners = local[faces]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    solid = lengths > 0
    triangles, normals = triangles[solid], normals[solid] / lengths[solid, None]
    pairs = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    sides = np.concatenate([normals, normals, normals])
    real = pairs[:, 0] != pairs[:, 1]
    pairs, sides = np.sort(pairs[real], axis=1), sides[real]
    if not len(pairs):
        return empty
    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    pairs, sides = pairs[order], sides[order]
    first = np.ones(len(pairs), dtype=bool)
    first[1:] = (pairs[1:] != pairs[:-1]).any(axis=1)
    starts = np.flatnonzero(first)
    count = np.diff(np.append(starts, len(pairs)))
    towards = (sides * np.repeat(sides[starts], count, axis=0)).sum(axis=1)
    # Faces bend where their normals part from the first face's. More than two faces whose
    # normals are all parallel or opposite are one flat face drawn from both sides, as
    # Archicad writes doors and windows, and the diagonals they share are no edges; two
    # opposite faces alone are where a sheet of no thickness ends, which is an edge.
    bend = np.where(np.repeat(count > 2, count), 1.0 - np.abs(towards), 1.0 - towards)  # noqa: PLR2004
    kept = pairs[starts[(count == 1) | (np.maximum.reduceat(bend, starts) > _FLAT)]]
    ends = welded * _WELD_M
    near, far = ends[kept[:, 0]], ends[kept[:, 1]]
    beyond = (near[:, 2] <= 0) | (far[:, 2] <= 0)
    near, far = near[beyond], far[beyond]
    for this, other in ((near, far), (far, near)):
        clip = this[:, 2] > 0
        share = this[clip, 2] / (this[clip, 2] - other[clip, 2])
        this[clip] += (other[clip] - this[clip]) * share[:, None]
        this[clip, 2] = 0.0
    return np.hstack([near, far])


#: How far a line drawn beyond the cut may lie from a product's edge and still be that
#: edge, in metres.
_EDGE_TOLERANCE_M = 0.005

#: A piece of a drawn line and whose edge it is: where it starts and ends as fractions
#: of the line, and the product's GlobalId.
Piece = tuple[float, float, str]


def line_owners(
    lines: NDArray[np.float64], edges: dict[str, NDArray[np.float64]]
) -> list[list[Piece]]:
    """Say whose edge each line drawn beyond the cut is, piece by piece.

    The serializer draws what the view sees beyond its cut with hidden lines removed, as
    anonymous segments, and one segment may run along the edges of several products in
    turn -- a door's jamb, then the wall above it. So each segment is split wherever an
    edge along it starts or ends, and each piece is the edge nearest the viewer that runs
    along it, since that edge hides the others.

    Args:
        lines: The segments, one row ``x0, y0, x1, y1`` each, in plane metres.
        edges: Each product's edges beyond the cut, by GlobalId (:func:`edges_beyond`).

    Returns:
        For each segment, its pieces in order, each with the product it is an edge of.
        A stretch that runs along no product's edge is left out, and so is one no
        longer than the tolerance an edge is matched within, which cannot say whose it
        is: a segment that short, or where one edge ends a hair before another.
    """
    guids = sorted(guid for guid, rows in edges.items() if len(rows))
    if not guids:
        return [[] for _ in lines]
    every = np.vstack([edges[guid] for guid in guids])
    owners_of = np.concatenate([np.full(len(edges[guid]), n) for n, guid in enumerate(guids)])
    tolerance = _EDGE_TOLERANCE_M
    ends = every[:, [0, 1]], every[:, [3, 4]]
    index = _edge_index(np.hstack([np.minimum(*ends) - tolerance, np.maximum(*ends) + tolerance]))
    owners: list[list[Piece]] = []
    for sx0, sy0, sx1, sy1 in lines:
        # Only the edges the index finds near the segment are tested; they keep their
        # order, so the nearest edge wins a tie exactly as it would among all of them.
        found = index.near((min(sx0, sx1), min(sy0, sy1), max(sx0, sx1), max(sy0, sy1)))
        table, owner = every[found], owners_of[found]
        x0, y0, d0, x1, y1, d1 = table.T
        run = math.hypot(sx1 - sx0, sy1 - sy0)
        if run <= tolerance:
            # Shorter than the tolerance an edge is matched within: it cannot say whose.
            owners.append([])
            continue
        near = (
            (np.minimum(x0, x1) - tolerance <= max(sx0, sx1))
            & (min(sx0, sx1) <= np.maximum(x0, x1) + tolerance)
            & (np.minimum(y0, y1) - tolerance <= max(sy0, sy1))
            & (min(sy0, sy1) <= np.maximum(y0, y1) + tolerance)
        )
        if run < 2.0 * tolerance:
            owners.append(
                _dot_owner(((sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0), table, near, guids, owner)
            )
            continue
        ux, uy = (sx1 - sx0) / run, (sy1 - sy0) / run
        # Both ends of an edge along the segment lie on its line; where along it they fall,
        # as fractions of the segment, says which stretch of it the edge covers.
        start = ((x0 - sx0) * ux + (y0 - sy0) * uy) / run
        end = ((x1 - sx0) * ux + (y1 - sy0) * uy) / run
        low = np.maximum(0.0, np.minimum(start, end))
        high = np.minimum(1.0, np.maximum(start, end))
        # An edge runs along the segment where it lies on the segment's line over the
        # stretch they share. Its offset from that line is measured at that stretch's two
        # ends, not at the edge's own: a long edge seen along a short segment would
        # otherwise be judged by how the segment's rounding tilts it metres away.
        offset0 = (x0 - sx0) * uy - (y0 - sy0) * ux
        offset1 = (x1 - sx0) * uy - (y1 - sy0) * ux
        span = np.where(end != start, end - start, 1.0)
        along = np.flatnonzero(
            near
            & (np.abs(offset0 + (low - start) / span * (offset1 - offset0)) <= tolerance)
            & (np.abs(offset0 + (high - start) / span * (offset1 - offset0)) <= tolerance)
            & ((high - low) * run > tolerance)
        )
        pieces: list[Piece] = []
        cuts = sorted({0.0, 1.0, *map(float, low[along]), *map(float, high[along])})
        for first, second in pairwise(cuts):
            if (second - first) * run <= tolerance:
                # Where one edge ends a hair before another along the same line: no
                # stretch this short can say whose it is.
                continue
            middle = (first + second) / 2.0
            cover = along[(low[along] <= middle) & (middle <= high[along])]
            if not len(cover):
                continue
            share = (middle - start[cover]) / (end[cover] - start[cover])
            depth = np.round(d0[cover] + share * (d1[cover] - d0[cover]), 4)
            guid = guids[owner[cover[int(np.argmax(depth))]]]
            if pieces and pieces[-1][2] == guid and (first - pieces[-1][1]) * run <= tolerance:
                pieces[-1] = (pieces[-1][0], second, guid)
            else:
                pieces.append((first, second, guid))
        owners.append(pieces)
    return owners


#: At most how many cells the edge index has along each side.
_INDEX_CELLS = 1024


@dataclass(frozen=True)
class _EdgeIndex:
    """A uniform grid over edges' boxes: each cell lists the edges whose box touches it.

    Attributes:
        low: The grid's lower-left corner, in plane metres.
        cell: A cell's side, in metres.
        size: How many cells there are along each side.
        keys: The cells that hold an edge, sorted, each as its column times ``size`` plus
            its row.
        starts: Where each of those cells' edges start in ``members``, then where the
            last cell's end.
        members: Edge indices, cell by cell, ascending within each cell.
    """

    low: tuple[float, float]
    cell: float
    size: int
    keys: NDArray[np.int64]
    starts: NDArray[np.int64]
    members: NDArray[np.int64]

    def cells(self, values: NDArray[np.float64], axis: int) -> NDArray[np.int64]:
        """Return the column (``axis`` 0) or row (1) of each coordinate.

        A coordinate off the grid takes the nearest border cell's, so a box reaching
        beyond the edges still finds those near its part on the grid.

        Args:
            values: Plane coordinates, in metres.
            axis: 0 for x, 1 for y.

        Returns:
            The cells' columns or rows.
        """
        scaled = np.floor((values - self.low[axis]) / self.cell)
        return np.clip(scaled, 0, self.size - 1).astype(np.int64)

    def near(self, box: tuple[float, float, float, float]) -> NDArray[np.int64]:
        """Return every edge whose box may touch a box, in ascending order.

        Every edge whose box overlaps ``box`` is among them, since both lie in a cell they
        share; an edge that shares only a cell with it may be too.

        Args:
            box: ``x_low, y_low, x_high, y_high`` in plane metres.

        Returns:
            The edges' indices.
        """
        (c0, c1), (r0, r1) = (
            self.cells(np.array([box[axis], box[axis + 2]]), axis) for axis in (0, 1)
        )
        wanted = (np.arange(c0, c1 + 1)[:, None] * self.size + np.arange(r0, r1 + 1)).ravel()
        at = np.searchsorted(self.keys, wanted)
        held = at < len(self.keys)
        held[held] = self.keys[at[held]] == wanted[held]
        chunks = [self.members[self.starts[i] : self.starts[i + 1]] for i in at[held]]
        if not chunks:
            return np.zeros(0, dtype=np.int64)
        return np.unique(np.concatenate(chunks))


def _edge_index(boxes: NDArray[np.float64]) -> _EdgeIndex:
    """Index edges by their boxes on a uniform grid, so a line meets only those near it.

    The grid has about as many cells as there are edges, and no cell is smaller than
    twice the tolerance an edge is matched within.

    Args:
        boxes: One row ``x_low, y_low, x_high, y_high`` per edge, in plane metres.

    Returns:
        The index.
    """
    low = boxes[:, :2].min(axis=0)
    span = float((boxes[:, 2:].max(axis=0) - low).max())
    size = min(_INDEX_CELLS, max(1, math.ceil(math.sqrt(len(boxes)))))
    grid = _EdgeIndex(
        low=(float(low[0]), float(low[1])),
        cell=max(span / size, 2.0 * _EDGE_TOLERANCE_M),
        size=size,
        keys=np.zeros(0, dtype=np.int64),
        starts=np.zeros(1, dtype=np.int64),
        members=np.zeros(0, dtype=np.int64),
    )
    c0, r0 = grid.cells(boxes[:, 0], 0), grid.cells(boxes[:, 1], 1)
    c1, r1 = grid.cells(boxes[:, 2], 0), grid.cells(boxes[:, 3], 1)
    columns = c1 - c0 + 1
    count = columns * (r1 - r0 + 1)
    edge = np.repeat(np.arange(len(boxes)), count)
    # Each edge's cells, numbered from 0 within its own rectangle of them.
    local = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    width = np.repeat(columns, count)
    key = (np.repeat(c0, count) + local % width) * size + np.repeat(r0, count) + local // width
    order = np.lexsort((edge, key))
    key, edge = key[order], edge[order]
    keys, starts = np.unique(key, return_index=True)
    return replace(
        grid, keys=keys, starts=np.append(starts, len(key)).astype(np.int64), members=edge
    )


def _dot_owner(
    point: tuple[float, float],
    table: NDArray[np.float64],
    near: NDArray[np.bool_],
    guids: list[str],
    owner: NDArray[np.int64],
) -> list[Piece]:
    """Say whose edge a segment too short to have a direction is: the nearest through it.

    Args:
        point: The segment's midpoint, in plane metres.
        table: Every edge, as :func:`edges_beyond` gives them.
        near: Which edges come near the segment at all.
        guids: The products, in the order ``owner`` numbers them.
        owner: The product each edge belongs to.

    Returns:
        One piece covering the whole segment, or none where no edge passes through it.
    """
    x0, y0, d0, x1, y1, d1 = table[near].T
    dx, dy = x1 - x0, y1 - y0
    span = dx * dx + dy * dy
    share = np.clip(
        ((point[0] - x0) * dx + (point[1] - y0) * dy) / np.where(span > 0, span, 1.0), 0.0, 1.0
    )
    # An edge seen end on draws nothing on the paper, so it owns no line either.
    through = (span > 0) & (
        np.hypot(x0 + share * dx - point[0], y0 + share * dy - point[1]) <= _EDGE_TOLERANCE_M
    )
    if not through.any():
        return []
    depth = np.where(through, np.round(d0 + share * (d1 - d0), 4), -np.inf)
    return [(0.0, 1.0, guids[owner[np.flatnonzero(near)[int(np.argmax(depth))]]])]


def _representation(guid: str, facts: ModelFacts, built: BuiltModel) -> Representation | None:
    """Say whether the cut passes through an element or it is seen beyond it.

    Args:
        guid: The element's GlobalId.
        facts: What the model says.
        built: Where the drawing is cut.

    Returns:
        ``cut`` or ``projection``, or None when the model gives no geometry.
    """
    tolerance = _CUT_TOLERANCE_M
    depth = facts.depths.get(guid)
    if depth is not None:
        return "cut" if depth[0] <= tolerance and depth[1] >= -tolerance else "projection"
    extent = facts.extents.get(guid)
    if extent is None:
        return None
    low, high = extent
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


def _seen_beyond(
    facts: ModelFacts,
    built: BuiltModel,
    frame: _Frame,
    drawn: list[Element],
    lines: list[list[Point]],
) -> list[Element]:
    """Describe what a view through an explicit plane sees beyond its cut.

    The serializer draws those lines itself -- below a plan's cut a stair, a low railing,
    the floor slab; beyond a section's the walls, doors and windows of the rooms it looks
    into -- but as anonymous paths in one group, with hidden lines removed. Each line is
    given to the product whose edge it is (:func:`line_owners`), and every product of the
    drawing that lies wholly beyond the cut is described by the lines it draws: they are
    its outlines, and its box is theirs. A product hidden behind another draws no line
    and is not described, and one partly hidden claims only what shows. Nothing is drawn
    here, because the serializer already drew it.

    Args:
        facts: What the model says, measured against the view's plane.
        built: The model and what is drawn of it.
        frame: The view's placement.
        drawn: The elements the cut passes through.
        lines: The lines the serializer draws beyond the cut, on the paper.

    Returns:
        One ``projection`` element per product seen beyond the cut.
    """
    segments = segments_of(lines)
    plane = np.array(
        [
            [*apply(frame.transform, x0, y0), *apply(frame.transform, x1, y1)]
            for x0, y0, x1, y1 in segments
        ],
        dtype=np.float64,
    ).reshape(-1, 4)
    own: dict[str, list[list[Point]]] = {}
    for (x0, y0, x1, y1), pieces in zip(
        segments, line_owners(plane * frame.unit_scale, facts.edges), strict=True
    ):
        for first, second, owner in pieces:
            own.setdefault(owner, []).append(
                [
                    (round(float(x0 + (x1 - x0) * at), 3), round(float(y0 + (y1 - y0) * at), 3))
                    for at in (first, second)
                ]
            )
    seen = {element.ifc_guid for element in drawn}
    added: list[Element] = []
    for guid in sorted(built.include or facts.depths):
        depth, paper = facts.depths.get(guid), own.get(guid)
        if guid in seen or guid not in facts.classes or depth is None or not paper:
            continue
        if depth[1] >= -_CUT_TOLERANCE_M:
            continue
        ifc_class, name = facts.classes[guid]
        added.append(
            Element(
                id=f"e-{len(drawn) + len(added):02d}",
                ifcGuid=guid,
                ifcClass=ifc_class,
                name=name,
                tag=facts.tags.get(guid),
                viewport=frame.viewport,
                paperBBox=bounding_box(point for loop in paper for point in loop),
                paperOutlines=paper,
                representation="projection",
                properties=cast("dict[str, Any] | None", facts.properties.get(guid)),
                provenance=Provenance.AUTHORED,
            )
        )
    return added


def _draw_swings(
    group: str,
    elements: list[Element],
    built: BuiltModel,
    frame: _Frame,
    placement: tuple[float, float, float],
) -> str:
    """Draw each swinging door open, with its arc, and make the swing part of the door.

    The swing goes into the door's own group, where an SVG reader finds it with the
    door, and into the door's outlines and box, because it is how the door is drawn.

    Args:
        group: The view group.
        elements: The elements drawn, whose doors gain their swings.
        built: The model and its plan's plane.
        frame: The view's placement.
        placement: The view group's offset on the sheet, x and y, and the sheet's height.

    Returns:
        The view group with the swings drawn.
    """
    doors = {e.ifc_guid: e for e in elements if e.ifc_guid and e.ifc_class.startswith("IfcDoor")}
    if not doors or built.section is None:
        return group
    swings, _ = door_swings(open_ifc(built.path), doors)
    x_axis, y_axis = built.section.axes()
    ox, oy, _ = built.section.location
    offset_x, offset_y, height = placement

    def paper(point: tuple[float, float]) -> tuple[float, float]:
        dx, dy = point[0] - ox, point[1] - oy
        return frame.paper(dx * x_axis[0] + dy * x_axis[1], dx * y_axis[0] + dy * y_axis[1])

    def path(line: Sequence[tuple[float, float]]) -> str:
        return " ".join(
            f"{'M' if index == 0 else 'L'}{x - offset_x:.3f},{height - y - offset_y:.3f}"
            for index, (x, y) in enumerate(line)
        )

    for guid, swing in swings.items():
        door = doors[guid]
        leaves = [[paper(point) for point in line] for line in swing.leaves]
        arcs = [[paper(point) for point in line] for line in swing.arcs]
        markup = "".join(
            f'<path d="{path(line)}" fill="none" stroke-width="{width}"/>'
            for lines, width in ((leaves, "0.18"), (arcs, "0.1"))
            for line in lines
        )
        found = re.search(rf'<g\b[^>]*ifc:guid="{re.escape(guid)}"[^>]*>', group)
        if found is not None:
            close = group.index("</g>", found.end())
            group = group[:close] + markup + group[close:]
        outlines = [*(door.paper_outlines or []), *leaves, *arcs]
        door.paper_outlines = outlines
        door.paper_bbox = bounding_box(point for line in outlines for point in line)
    return group


def _styles(
    elements: list[Element], facts: ModelFacts, material_styles: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """Decide how each cut element is drawn: solid, grey, outlined or not at all.

    Args:
        elements: The elements drawn.
        facts: What the model says about each: its load-bearing property, its materials
            and what it is part of.
        material_styles: How a part is drawn by its materials, as
            :attr:`SheetSpec.material_styles` says.

    Returns:
        A key of :data:`plannotation.export.drafting.STYLES` by GlobalId.
    """
    styles: dict[str, str] = {}
    for element in elements:
        if element.ifc_guid is None:
            continue
        styles[element.ifc_guid] = style_of(
            element.ifc_class,
            load_bearing=_load_bearing(element.properties or {}),
            is_a=facts.is_a,
            materials=facts.materials.get(element.ifc_guid, ()),
            aggregate=facts.aggregates.get(element.ifc_guid),
            material_styles=material_styles,
        )
    return styles


def _seen_lines(svg: str, height_mm: float, offset: tuple[float, float]) -> list[list[Point]]:
    """Read the lines the serializer draws beyond the cut, on the paper.

    They belong to no product, and a label laid over one is as unreadable as one laid
    over a wall.

    Args:
        svg: The serializer's output.
        height_mm: The sheet height.
        offset: Where the view group sits on the sheet.

    Returns:
        One polyline per path of the projection group.
    """
    start = svg.find('<g class="projection">')
    if start == -1:
        return []
    body = svg[start : svg.find("</g>", start)]
    return [
        path_points(d, page_height_mm=height_mm, offset=offset)
        for d in re.findall(r'\bd="([^"]+)"', body)
    ]


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
def _draw_grids(
    sheet: Sheet,
    grids: tuple[GridAxis, ...],
    frame: _Frame,
    box: Box,
    *,
    content: Sequence[float] | None = None,
    overshoot: float = 0.0,
    dash: Sequence[float] | None = None,
) -> list[Annotation]:
    """Draw the grid lines and their bubbles, and describe them.

    Args:
        sheet: The sheet being composed.
        grids: The grid lines.
        frame: The view's placement.
        box: The viewport's paper box, which the lines span unless ``content`` is given.
        content: The drawing's box; when given, each line runs ``overshoot`` beyond it
            and its bubble sits past that.
        overshoot: How far a line runs beyond the drawing, in millimetres.
        dash: The chain line's pattern, or None for a continuous line.

    Returns:
        One ``grid`` annotation per line.
    """
    annotations: list[Annotation] = []
    low_x, low_y, high_x, high_y = box
    if content is not None:
        low_x = content[0] - overshoot - BUBBLE_OFFSET_MM - 4.0
        low_y = content[1] - overshoot - 4.0
        high_x = content[2] + overshoot + 4.0
        high_y = content[3] + overshoot + BUBBLE_OFFSET_MM + 4.0
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
        if dash is None:
            sheet.line(*start, *end, width=0.18)
        else:
            sheet.line(*start, *end, width=0.13, dash=dash)
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
    sheet: Sheet,
    levels: Sequence[Level],
    frame: _Frame,
    *,
    left: float,
    datum: float = 0.0,
    standing: bool = False,
) -> list[Annotation]:
    """Mark each storey's level beside a section, from the model's elevations.

    The mark stands at the storey's z in the model and prints its height above the
    building's ±0,00, which is what a level mark states and what its ``elevation``
    records. The two differ wherever the model places that datum away from z = 0: a
    ground floor at z = 14.30 m reads ±0,00.

    The mark's box is centred on the level line, so that the point it stands for is
    the height it states: a reader taking the box's centre through ``paperToPlane``
    lands on the storey's floor.

    Args:
        sheet: The sheet being composed.
        levels: The storeys to mark, lowest first.
        frame: The view's placement.
        left: The drawing's left edge on the paper.
        datum: The z of the building's ±0,00 in model coordinates, in metres.
        standing: Stand an open triangle on the line, point down, as a level mark on a
            section is drawn; otherwise hang one below it, as the samples do.

    Returns:
        One ``level`` annotation per storey.
    """
    annotations: list[Annotation] = []
    right = left - 2.0
    start = right - LEVEL_LENGTH_MM
    for index, level in enumerate(levels):
        plane_y = level.elevation - frame.origin_z * frame.unit_scale
        y = frame.paper_y(plane_y)
        above = round(level.elevation - datum, 6)
        text = level_text(above)
        if standing:
            draw_level_mark(sheet, (start, y), right - start, text)
        else:
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
                elevation=above,
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
    sheet: Sheet,
    grids: tuple[GridAxis, ...],
    frame: _Frame,
    content: Sequence[float],
    offsets: tuple[float, float] = DIMENSION_OFFSETS_MM,
) -> list[Annotation]:
    """Dimension each bay between consecutive grid lines, then the overall run.

    The value is the distance the model places between the two grids, in millimetres.
    It is not measured off the drawing, which is what makes it authored.

    Args:
        sheet: The sheet being composed.
        grids: The grid lines.
        frame: The view's placement.
        content: The drawing's box on the paper; the chains run just outside it.
        offsets: How far from the drawing the bay chain and the overall dimension run.

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
            outward = offsets[0 if index < len(line) - 1 else 1]
            if vertical:
                y = content[1] - outward
                start = (frame.paper_x(first.position), y)
                end = (frame.paper_x(second.position), y)
            else:
                x = content[2] + outward
                start = (x, frame.paper_y(first.position))
                end = (x, frame.paper_y(second.position))
            joint = "" if len(first.axis) == len(second.axis) == 1 else "-"
            annotations.append(
                _dimension(
                    sheet,
                    f"a-dim-{first.axis}{joint}{second.axis}",
                    start,
                    end,
                    abs(second.position - first.position) * 1000.0,
                    (f"g-{first.axis}", f"g-{second.axis}"),
                    frame.viewport,
                )
            )
    return annotations


def _draw_level_dimensions(
    sheet: Sheet,
    marks: list[Annotation],
    levels: Sequence[Level],
    content: Sequence[float],
    offsets: tuple[float, float] = DIMENSION_OFFSETS_MM,
) -> list[Annotation]:
    """Dimension each storey height between level marks, then the overall height.

    Args:
        sheet: The sheet being composed.
        marks: The level annotations, in the order of ``levels``.
        levels: The storeys they mark.
        content: The drawing's box on the paper; the chain runs to its right.
        offsets: How far from the drawing the storey chain and the overall run.

    Returns:
        The ``dimension`` annotations; none when there are fewer than two levels.
    """
    annotations: list[Annotation] = []
    pairs = list(pairwise(range(len(levels))))
    if len(levels) > 2:  # noqa: PLR2004 - an overall dimension needs more than one storey
        pairs.append((0, len(levels) - 1))
    for index, (low, high) in enumerate(pairs):
        outward = offsets[0 if index < len(levels) - 1 else 1]
        x = content[2] + outward
        start = (x, (marks[low].paper_bbox[1] + marks[low].paper_bbox[3]) / 2.0)
        end = (x, (marks[high].paper_bbox[1] + marks[high].paper_bbox[3]) / 2.0)
        annotations.append(
            _dimension(
                sheet,
                f"a-dim-lvl-{low}{'' if high < 10 else '-'}{high}",  # noqa: PLR2004 - digits
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


def _draw_callout(sheet: Sheet, target_sheet: str, viewport: str, box: Box) -> Annotation:
    """Draw a callout pointing at another sheet, in the viewport's lower right corner.

    Args:
        sheet: The sheet being composed.
        target_sheet: The sheet id it refers to.
        viewport: The viewport's local id.
        box: The viewport's paper box.

    Returns:
        The ``callout`` annotation.
    """
    centre = (box[2] - 12.0, box[1] + 8.0)
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


#: How much wider than its text a level mark is: the triangle, and a gap.
LEVEL_SYMBOL_MM = 2 * LEVEL_TRIANGLE_MM[0] + 4.0

#: How far beyond the drawing a section mark's bubble stands, edge to edge.
_MARK_CLEARANCE_MM = 6.0


def _draw_section_marks(
    sheet: Sheet, marks: Sequence[SectionMark], frame: _Frame, elements: Sequence[Element]
) -> list[Annotation]:
    """Mark each section's cutting line at both ends, just clear of the building.

    Each end stands beyond the last element the line crosses, so that its heavy stub
    runs up to the building where the cut enters it, not to the edge of the sheet.

    Args:
        sheet: The sheet being composed.
        marks: The sections.
        frame: The view's placement.
        elements: The elements drawn; spaces are passed over, since walls bound them.

    Returns:
        Two ``sectionMark`` annotations per section, each targeting the section's sheet.
    """
    annotations: list[Annotation] = []
    reach = MARK_RADIUS_MM + _MARK_CLEARANCE_MM
    solid = [e.paper_bbox for e in elements if e.ifc_class != "IfcSpace"]
    for mark in marks:
        if mark.vertical:
            x = frame.paper_x(mark.position)
            crossed = [box for box in solid if box[0] <= x <= box[2]] or solid
            low, high = min(b[1] for b in crossed), max(b[3] for b in crossed)
            ends = [((x, high + reach), (x, high)), ((x, low - reach), (x, low))]
            looking = (float(mark.looking), 0.0)
        else:
            y = frame.paper_y(mark.position)
            crossed = [box for box in solid if box[1] <= y <= box[3]] or solid
            low, high = min(b[0] for b in crossed), max(b[2] for b in crossed)
            ends = [((low - reach, y), (low, y)), ((high + reach, y), (high, y))]
            looking = (0.0, float(mark.looking))
        for index, (centre, toward) in enumerate(ends, start=1):
            box, stub = draw_section_mark(sheet, centre, toward, looking, mark)
            annotations.append(
                Annotation(
                    id=f"a-sec-{mark.label}-{index}",
                    type="sectionMark",
                    viewport=frame.viewport,
                    paperBBox=box,
                    text=mark.label,
                    target=Target(sheetId=mark.target_sheet, detail=mark.label),
                    geometry=[(round(p[0], 3), round(p[1], 3)) for p in stub],
                    provenance=Provenance.AUTHORED,
                )
            )
    return annotations


#: The sizes a room label is tried at, largest first: number, then name and area.
_ROOM_SIZES = ((2.8, 2.2), (2.2, 1.8))


def _room_lines(
    number: str | None, name: str | None, area: str | None
) -> list[list[tuple[str, Line]]]:
    """Return the ways a room's label may be set, fullest first.

    Args:
        number: The room's number, its ``Name``.
        name: Its name, its ``LongName``.
        area: Its area as printed.

    Returns:
        Each variant as ``(part, line)`` pairs, the part being ``number``, ``name`` or
        ``area``: every size of the full label, then every size without the name, then
        the number alone.
    """
    full: list[list[tuple[str, Line]]] = []
    for big, small in _ROOM_SIZES:
        label = [("number", Line(number, big, bold=True))] if number else []
        label += [("name", Line(name, small))] if name else []
        label += [("area", Line(area, small))] if area else []
        full.append(label)
    nameless = [[part for part in label if part[0] != "name"] for label in full] if name else []
    alone = [[("number", Line(number, _ROOM_SIZES[-1][0], bold=True))]] if number else []
    return [variant for variant in (*full, *nameless, *alone) if variant]


def _draw_rooms(
    sheet: Sheet,
    rooms: RoomLabels,
    elements: list[Element],
    drawn: list[Annotation],
    beyond: tuple[list[list[Point]], ModelFacts],
    frame: _Frame,
    *,
    floor: tuple[float, str | None] | None = None,
) -> list[Annotation]:
    """Label each room with its number, name and area, inside the room.

    A label goes where it fits inside the room clear of every line drawn there -- walls,
    doors and their swings, what is seen below the cut, grid and section lines -- and of
    the other labels. Where the full label does not fit, a smaller one is tried; then
    the full label over a grid line, which a label may cross where it must, rather than
    lose the room's name; then one without the name, then the number alone.

    Args:
        sheet: The sheet being composed.
        rooms: How rooms are labelled.
        elements: The elements drawn, the rooms among them.
        drawn: The annotations already on the sheet.
        beyond: The lines drawn beyond the cut, and what the model says, each room's
            ``LongName`` among it.
        frame: The view's placement.
        floor: The storey's height above ±0,00 and its GlobalId, to mark its level in
            the largest room; None for no level mark.

    Returns:
        A ``tag`` for each room's number and a ``text`` for its name and area, each
        showing the property it prints.
    """
    seen_lines, facts = beyond
    spaces = [e for e in elements if e.ifc_class == "IfcSpace" and e.paper_outlines]
    others = [e for e in elements if e.ifc_class != "IfcSpace"]
    lines = [outline for e in others for outline in e.paper_outlines or []]
    lines += seen_lines
    lines += [a.geometry for a in drawn if a.geometry and a.annotation_type == "sectionMark"]
    walls = segments_of(lines)
    grids = segments_of([a.geometry for a in drawn if a.geometry and a.annotation_type == "grid"])
    obstacles = np.vstack([walls, grids])
    placed: list[tuple[float, float, float, float]] = []
    annotations: list[Annotation] = []
    for space in sorted(spaces, key=lambda e: (e.name or "", e.local_id)):
        outline = max(space.paper_outlines or [], key=_area)
        polygon = [(p[0], p[1]) for p in outline]
        area = area_text(_property(space, rooms.area_property), rooms.area_unit)
        number, long_name = facts.names.get(space.ifc_guid or "", (space.name, None))
        own = segments_of([[*polygon, polygon[0]]])
        variants = _room_lines(number, long_name, area)
        named = [v for v in variants if any(part == "name" for part, _ in v)]
        rest = [v for v in variants if v not in named]
        clear, over = np.vstack([obstacles, own]), np.vstack([walls, own])
        attempts = [
            *((v, clear) for v in named),
            *((v, over) for v in named),
            *((v, clear) for v in rest),
            *((v, over) for v in rest),
        ]
        for variant, avoiding in attempts:
            texts = [line for _, line in variant]
            centre = place_label(polygon, texts, avoiding, placed)
            if centre is None:
                continue
            boxes = draw_label(sheet, centre, texts)
            placed.append(bounding_box([corner for box in boxes for corner in (box[:2], box[2:])]))
            for (part, line), box in zip(variant, boxes, strict=True):
                annotations.append(
                    Annotation(
                        id=f"a-room-{space.local_id}" + ("" if part == "number" else f"-{part}"),
                        type="tag" if part == "number" else "text",
                        viewport=frame.viewport,
                        paperBBox=box,
                        text=line.text,
                        shows=Shows(
                            element=space.local_id,
                            property={
                                "number": "Name",
                                "name": "LongName",
                                "area": rooms.area_property,
                            }[part],
                        ),
                        provenance=Provenance.AUTHORED,
                    )
                )
            break
    if floor is not None and spaces:
        largest = max(spaces, key=lambda e: max(map(_area, e.paper_outlines or [[]])))
        polygon = [(p[0], p[1]) for p in max(largest.paper_outlines or [], key=_area)]
        mark = _draw_floor_level(sheet, polygon, obstacles, placed, floor, frame)
        if mark is not None:
            annotations.append(mark)
    return annotations


def _draw_floor_level(
    sheet: Sheet,
    polygon: list[Point],
    obstacles: NDArray[np.float64],
    placed: list[tuple[float, float, float, float]],
    floor: tuple[float, str | None],
    frame: _Frame,
) -> Annotation | None:
    """Mark a plan's floor level inside a room, clear of its label and its lines.

    Args:
        sheet: The sheet being composed.
        polygon: The room's outline.
        obstacles: Every line drawn near it.
        placed: The labels already placed.
        floor: The storey's height above ±0,00, in metres, and its GlobalId.
        frame: The view's placement.

    Returns:
        The ``level`` annotation, or None where the mark fits nowhere in the room.
    """
    height, guid = floor
    text = level_text(height)
    line = Line(text, 2.2)
    own = segments_of([[*polygon, polygon[0]]])
    centre = place_label(
        polygon, [line], np.vstack([obstacles, own]), placed, extra_width=LEVEL_SYMBOL_MM
    )
    if centre is None:
        return None
    width = text_width(text, line.size) + LEVEL_SYMBOL_MM
    start = (centre[0] - width / 2.0, centre[1] - 1.1)
    box, geometry = draw_level_mark(sheet, start, width, text, size=line.size)
    placed.append(box)
    return Annotation(
        id="lvl-floor",
        type="level",
        viewport=frame.viewport,
        paperBBox=(round(box[0], 3), round(box[1], 3), round(box[2], 3), round(box[3], 3)),
        text=text,
        elevation=height,
        ifcGuid=guid,
        geometry=[(round(x, 3), round(y, 3)) for x, y in geometry],
        provenance=Provenance.AUTHORED,
    )


def _property(element: Element, path: str | None) -> object:
    """Return one property of an element, named ``PsetName.Property``.

    Args:
        element: The element.
        path: The property's dotted name, or None.

    Returns:
        Its value, or None where the element does not carry it.
    """
    if not path or "." not in path:
        return None
    pset, _, name = path.partition(".")
    values = element.pset(pset)
    return None if values is None else values.get(name)


def _area(outline: Sequence[Sequence[float]]) -> float:
    """Return the area a closed outline encloses, by the shoelace formula.

    Args:
        outline: Its vertices.

    Returns:
        The area, unsigned.
    """
    points = list(outline)
    return (
        abs(
            sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(points, [*points[1:], points[0]], strict=True)
            )
        )
        / 2.0
    )


def _draw_view_furniture(
    sheet: Sheet,
    spec: SheetSpec,
    facts: ModelFacts,
    frame: _Frame,
    content: Sequence[float],
    *,
    axes: tuple[Sequence[float], Sequence[float]] | None,
) -> list[Annotation]:
    """Draw the view's title, its scale bar and, on a plan, the north arrow.

    The title goes under the drawing, below the dimension chains; the scale bar beside
    it; the north arrow above the drawing's right-hand end.

    Args:
        sheet: The sheet being composed.
        spec: What the sheet carries.
        facts: What the model says, true north among it.
        frame: The view's placement.
        content: The drawing's box on the paper.
        axes: A plan's plane x- and y-axis, which place north on its paper; None for a
            section, which has no north arrow.

    Returns:
        The title's callout and text, the scale bar and the north arrow, as annotations.
    """
    annotations: list[Annotation] = []
    scale = f"1:{spec.scale:.0f}"
    # Below the dimension chains, and below the grid lines' ends where they run past them.
    below = max(spec.dimension_offsets_mm[1] + 16.0, (spec.grid_overshoot_mm or 0.0) + 12.0)
    baseline = content[1] - below
    right = content[0]
    if spec.view_title is not None:
        title = spec.view_title
        name, bubble, scale_box = draw_view_title(sheet, content[0], baseline, title, scale)
        right = max(name[2], scale_box[2])
        if bubble is not None and title.target_sheet:
            annotations.append(
                Annotation(
                    id="a-view-callout",
                    type="callout",
                    viewport=frame.viewport,
                    paperBBox=bubble,
                    text=title.label,
                    target=Target(sheetId=title.target_sheet, detail=title.label),
                    provenance=Provenance.AUTHORED,
                )
            )
        for identifier, text, box in (
            ("a-view-title", title.text, name),
            ("a-view-scale", scale, scale_box),
        ):
            annotations.append(
                Annotation(
                    id=identifier,
                    type="text",
                    viewport=frame.viewport,
                    paperBBox=(box[0], box[1], box[2], box[3]),
                    text=text,
                    provenance=Provenance.AUTHORED,
                )
            )
    if spec.scale_bar:
        box, axis, text = draw_scale_bar(sheet, right + 20.0, baseline - 3.5, spec.scale)
        annotations.append(
            Annotation(
                id="a-scale-bar",
                type="scaleBar",
                viewport=frame.viewport,
                paperBBox=box,
                text=text,
                value=10,
                unit="m",
                geometry=axis,
                provenance=Provenance.AUTHORED,
            )
        )
    if spec.north_arrow and axes is not None and spec.grid_overshoot_mm is not None:
        north = _north_on_paper(facts, axes)
        centre = (
            content[2] + spec.grid_overshoot_mm + NORTH_RADIUS_MM + 16.0,
            content[3] + spec.grid_overshoot_mm - NORTH_RADIUS_MM,
        )
        box, axis = draw_north_arrow(sheet, centre, north)
        annotations.append(
            Annotation(
                id="a-north",
                type="northArrow",
                viewport=frame.viewport,
                paperBBox=box,
                text="N",
                geometry=[(round(p[0], 3), round(p[1], 3)) for p in axis],
                provenance=Provenance.AUTHORED,
            )
        )
    return annotations


def _north_on_paper(
    facts: ModelFacts, axes: tuple[Sequence[float], Sequence[float]]
) -> tuple[float, float]:
    """Return the direction of true north on a plan's paper.

    A plan's paper runs along its plane's axes, x to the right and y up, so north on the
    paper is north's components along those two.

    Args:
        facts: What the model says, true north among it.
        axes: The plan's plane x- and y-axis.

    Returns:
        A unit vector on the paper.
    """
    north = facts.north
    x = north[0] * axes[0][0] + north[1] * axes[0][1]
    y = north[0] * axes[1][0] + north[1] * axes[1][1]
    length = math.hypot(x, y) or 1.0
    return (x / length, y / length)


def _draw_title_block(sheet: Sheet, spec: SheetSpec, project: Project | None) -> None:
    """Draw the title block: the compact one, or labelled fields when the spec has them.

    Args:
        sheet: The sheet being composed.
        spec: What the sheet says about itself.
        project: The project it belongs to, or None.
    """
    box = title_block_box(sheet.width_mm, sheet.height_mm, spec.title_block_mm)
    sheet.rect(box)
    if spec.title_fields:
        _draw_title_fields(sheet, box, spec)
        return
    wording = spec.wording
    if project is not None and project.name:
        sheet.text(box[0] + 4.0, box[1] + 46.0, project.name, size=5.0)
    if project is not None and project.number:
        sheet.text(box[0] + 4.0, box[1] + 38.0, f"{wording.project} {project.number}", size=3.0)
    sheet.text(box[0] + 4.0, box[1] + 26.0, spec.title, size=4.0)
    sheet.text(box[0] + 4.0, box[1] + 8.0, spec.sheet_id, size=6.0)
    scale = wording.scale.format(denominator=f"{spec.scale:.0f}")
    sheet.text(box[2] - 4.0, box[1] + 8.0, scale, size=4.0, anchor="end")
    if spec.revision is not None:
        revision = f"{wording.revision} {spec.revision}"
        sheet.text(box[2] - 4.0, box[1] + 20.0, revision, size=3.0, anchor="end")


#: A title block field's label size, value size, line spacing and padding, in mm.
_FIELD_LABEL_MM, _FIELD_VALUE_MM, _FIELD_LEADING, _FIELD_PAD_MM = 1.8, 2.6, 1.3, 1.6

#: How wide a character of Helvetica is, on average, as a share of its size. Used only
#: to wrap a long value; generous, so that a wrapped line never overruns its field.
_CHAR_WIDTH_EM = 0.56


def wrap(text: str, width_mm: float, size_mm: float) -> list[str]:
    """Break a value into lines that fit a field, at spaces.

    Args:
        text: The value.
        width_mm: The width it has.
        size_mm: Its font size.

    Returns:
        Its lines, at least one.
    """
    limit = max(1, int(width_mm / (size_mm * _CHAR_WIDTH_EM)))
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if len(candidate) > limit and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _draw_title_fields(sheet: Sheet, box: Box, spec: SheetSpec) -> None:
    """Draw a title block of labelled fields, one below the other, the last two side by side.

    The last two fields -- the sheet number and the scale, by convention -- share the
    bottom row, the sheet number large. Every other field takes the block's width.

    Args:
        sheet: The sheet being composed.
        box: The title block's paper box.
        spec: What the sheet says about itself.
    """
    left, bottom, right, top = box
    width = right - left - 2 * _FIELD_PAD_MM
    *rows, number, scale = spec.title_fields
    y = top
    for row in rows:
        lines = wrap(row.value, width, _FIELD_VALUE_MM)
        height = (
            _FIELD_PAD_MM
            + _FIELD_LABEL_MM
            + len(lines) * _FIELD_VALUE_MM * _FIELD_LEADING
            + _FIELD_PAD_MM
        )
        label = y - _FIELD_PAD_MM - _FIELD_LABEL_MM
        sheet.text(left + _FIELD_PAD_MM, label, row.label, size=_FIELD_LABEL_MM)
        for index, line in enumerate(lines):
            baseline = label - (index + 1) * _FIELD_VALUE_MM * _FIELD_LEADING
            sheet.text(left + _FIELD_PAD_MM, baseline, line, size=_FIELD_VALUE_MM)
        y -= height
        sheet.line(left, y, right, y, width=0.18)
    split = left + (right - left) * 0.62
    sheet.line(split, bottom, split, y, width=0.18)
    for x0, cell, size in ((left, number, 7.0), (split, scale, 4.5)):
        label = y - _FIELD_PAD_MM - _FIELD_LABEL_MM
        sheet.text(x0 + _FIELD_PAD_MM, label, cell.label, size=_FIELD_LABEL_MM)
        baseline = bottom + (label - bottom) / 2 - size * 0.35
        sheet.text(x0 + _FIELD_PAD_MM, baseline, cell.value, size=size)


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------
def _ground_truth(
    plannotation: Plannotation,
    sheet_id: str,
    grids: tuple[GridAxis, ...],
    is_a: Callable[[str, str], bool] | None = None,
) -> list[Question]:
    """Derive questions whose answers come from the model, not from the drawing.

    Every question but one kind is about what the sheet shows, so a reader of the
    page alone can in principle answer it; the plannotation is meant to make that
    easier, not possible. The exception is the GlobalId of a marked element: only the
    plannotation carries it, and those questions say so with ``requiresPlannotation``.

    A count counts subtypes: an ``IfcWallStandardCase`` is a wall, and a sheet of 140
    of them answering "how many walls" with the 12 plain ``IfcWall`` among them would
    make a benchmark mark every correct reader wrong.

    Args:
        plannotation: The sheet's plannotation.
        sheet_id: The sheet number.
        grids: The grid lines.
        is_a: Says whether a class is a given class or a subtype of it; None matches
            classes exactly.

    Returns:
        One question per fact worth asking about, in a stable order.
    """
    elements = plannotation.elements or []
    annotations = plannotation.annotations or []

    def counted(ifc_class: str) -> int:
        match = is_a or (lambda cls, ancestor: cls == ancestor)
        return sum(1 for element in elements if match(element.ifc_class, ifc_class))

    questions: list[Question] = [
        {
            "sheet": sheet_id,
            "category": "count",
            "question": (
                f"How many {_PLURAL[ifc_class]} ({ifc_class}) are drawn on sheet {sheet_id}?"
            ),
            "answer": counted(ifc_class),
        }
        for ifc_class in _PLURAL
        if counted(ifc_class) or ifc_class == "IfcWall"
    ]
    questions += [
        {
            "sheet": sheet_id,
            "category": "sheet",
            "question": f"What is the drawing scale of sheet {sheet_id}? Answer as 1:n.",
            "answer": plannotation.sheet.scale,
        },
        {
            "sheet": sheet_id,
            "category": "sheet",
            "question": (
                f"What kind of drawing is sheet {sheet_id}? Answer with one of: "
                f"{', '.join(_DRAWING_TYPES)}."
            ),
            "answer": plannotation.sheet.drawing_type,
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
                "requiresPlannotation": True,
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
        exported: The composed sheet and its plannotation.
        built: The model it was drawn from.
        out_dir: The directory to write into, which is created.
        mod_date: The timestamp to stamp, so the output is reproducible.
        inkscape_fallback: Convert with Inkscape when CairoSVG cannot run.

    Returns:
        The plannotated PDF's path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sheet.svg").write_text(exported.svg, encoding="utf-8")
    plannotations_path = out_dir / "plannotations.json"
    plannotations_path.write_text(canonical_json(exported.plannotation), encoding="utf-8")

    width = exported.plannotation.page.width_mm
    height = exported.plannotation.page.height_mm
    plain = svg_to_pdf(
        exported.svg,
        out_dir / "sheet.pdf",
        width_mm=width,
        height_mm=height,
        mod_date=mod_date,
        inkscape_fallback=inkscape_fallback,
    )
    plannotated = out_dir / "sheet.plannotated.pdf"
    index = embed.build_index([exported.plannotation])
    embed.attach(plain, [exported.plannotation], index, plannotated, mod_date=mod_date)

    (out_dir / "groundtruth.jsonl").write_text(
        "".join(
            json.dumps(question, ensure_ascii=False, sort_keys=True) + "\n"
            for question in exported.ground_truth
        ),
        encoding="utf-8",
    )
    if built.path.resolve() != (out_dir / "model.ifc").resolve():
        (out_dir / "model.ifc").write_bytes(built.path.read_bytes())
    return plannotated
