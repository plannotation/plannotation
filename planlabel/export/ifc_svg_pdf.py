# SPDX-License-Identifier: Apache-2.0
"""The authored exporter: a model in, a labelled drawing out.

Every value this writes is computed from the model, which is what makes the output
``authored`` rather than ``inferred`` (SPEC 4.6). A dimension's value is the distance
between two grid lines as the model places them, not a number measured off a picture;
an element's ``paperBBox`` is its drawn geometry through the verified paper transform;
a tag prints the ``Tag`` the model holds. Nothing here reads the drawing to find out
what the drawing says.

The sheet is composed rather than rendered whole: the serializer draws the building,
and this module puts a frame, a title block, grid lines and bubbles, dimensions, tags
and a callout around it. Those are the parts a label has something to say about, and a
sample without them would exercise nothing beyond L2.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, cast

from planlabel.constants import SCHEMA_VERSION
from planlabel.errors import ExportError
from planlabel.export.geometry import bounding_box, path_points, union_box
from planlabel.export.paper import Affine, invert, paper_to_plane, plane_from_ifc_plane
from planlabel.export.sheet import PAPER_SIZES, Sheet, frame_box, title_block_box
from planlabel.export.svg_render import RenderedView, render_view
from planlabel.export.to_pdf import svg_to_pdf
from planlabel.model import (
    Annotation,
    Element,
    Generator,
    LengthUnit,
    Model,
    Page,
    PageLabel,
    Plane,
    Project,
    Provenance,
    Shows,
    Storey,
    Target,
    Viewport,
    canonical_json,
    conformance_level,
)
from planlabel.model import (
    Sheet as SheetInfo,
)
from planlabel.pdf import embed

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from planlabel.export.models import BuiltModel

#: Where the drawing sits on the sheet, as a paper bounding box on an A3 page.
VIEWPORT_BOX = (25.0, 85.0, 325.0, 285.0)

#: How far a grid bubble sits beyond the building, in paper millimetres.
BUBBLE_OFFSET_MM = 12.0

#: A grid bubble's radius, in paper millimetres.
BUBBLE_RADIUS_MM = 4.0

#: Fewest points a path must have before it is worth recording as an outline.
_MIN_OUTLINE_POINTS = 2


@dataclass(frozen=True)
class GridAxis:
    """One grid line, placed in the model and drawn on the paper.

    Attributes:
        axis: The label printed in the bubble, such as ``A`` or ``1``.
        vertical: True for a line of constant model x, drawn up the page.
        position: Its model coordinate along the axis it is constant in.
    """

    axis: str
    vertical: bool
    position: float


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
    ground_truth: tuple[dict[str, object], ...]


def export_sheet(
    built: BuiltModel,
    *,
    sheet_id: str,
    title: str,
    scale_denominator: float,
    grids: tuple[GridAxis, ...],
    callout_to: str,
    generator_version: str,
    page_size: str = "A3",
) -> ExportedSheet:
    """Draw one model on one sheet and describe it.

    Args:
        built: The model to draw.
        sheet_id: The sheet number as it will print.
        title: The sheet title as it will print.
        scale_denominator: The drawing scale's denominator.
        grids: The grid lines to draw and dimension between.
        callout_to: The sheet id the callout points at.
        generator_version: The version to record in ``generator``.
        page_size: A key of :data:`planlabel.export.sheet.PAPER_SIZES`.

    Returns:
        The composed sheet, its label, and its ground truth.

    Raises:
        ExportError: If the drawing does not fit the viewport it was given.
    """
    width_mm, height_mm = PAPER_SIZES[page_size]
    box_width = VIEWPORT_BOX[2] - VIEWPORT_BOX[0]
    box_height = VIEWPORT_BOX[3] - VIEWPORT_BOX[1]
    view = render_view(
        built.path,
        section_height=built.cut_height,
        scale_denominator=scale_denominator,
        width_mm=box_width,
        height_mm=box_height,
    )

    sheet = Sheet(width_mm=width_mm, height_mm=height_mm)
    sheet.rect(frame_box(width_mm, height_mm))

    # The serializer draws into its own box, whose top-left goes at the viewport's
    # top-left on the sheet. In SVG that is a downward y, so the paper top edge.
    offset = (VIEWPORT_BOX[0], sheet.y(VIEWPORT_BOX[3]))
    drawing = _view_group(view.svg)
    sheet.group(drawing, transform=f"translate({offset[0]},{offset[1]})")

    transform = paper_to_plane(
        view.matrix3, height_mm, _unit_scale(built.length_unit), svg_offset=offset
    )
    origin, x_axis, y_axis = plane_from_ifc_plane(view.ifc_plane, _unit_scale(built.length_unit))

    elements = _elements(view, height_mm, offset)
    if not elements:
        msg = f"sheet {sheet_id} drew no elements; the view would be empty"
        raise ExportError(msg)

    annotations: list[Annotation] = []
    annotations += _draw_grids(sheet, grids, transform)
    annotations += _draw_dimensions(sheet, grids, transform, built.length_unit)
    annotations += _draw_tags(sheet, elements)
    annotations.append(_draw_callout(sheet, callout_to))
    _draw_title_block(sheet, width_mm, height_mm, sheet_id, title, scale_denominator)

    contents = [element.paper_bbox for element in elements]
    contents += [
        annotation.paper_bbox for annotation in annotations if annotation.viewport == "vp-plan"
    ]
    viewport = Viewport(
        id="vp-plan",
        name=title,
        kind="plan",
        scale=scale_denominator,
        paperBBox=union_box(contents),
        plane=Plane(
            origin=(origin[0], origin[1], origin[2]),
            xAxis=(x_axis[0], x_axis[1], x_axis[2]),
            yAxis=(y_axis[0], y_axis[1], y_axis[2]),
        ),
        paperToPlane=transform,
        cutHeight=built.cut_height,
        storey=Storey(name="Erdgeschoss", elevation=built.storey_elevation),
    )
    label = PageLabel(
        planlabel=SCHEMA_VERSION,
        generator=Generator(name="planlabel", version=generator_version),
        provenance=Provenance.AUTHORED,
        page=Page(index=0, widthMm=width_mm, heightMm=height_mm),
        sheet=SheetInfo(
            id=sheet_id,
            title=title,
            revision="A",
            discipline="architecture",
            drawingType="plan",
            scale=scale_denominator,
            project=Project(name="Wohnanlage Lindenhof", number="2024-118"),
            titleBlockBBox=title_block_box(width_mm, height_mm),
        ),
        model=Model(
            file=built.path.name,
            lengthUnit=cast("LengthUnit", built.length_unit),
            schema="IFC4",
        ),
        viewports=[viewport],
        elements=elements,
        annotations=annotations,
    )
    return ExportedSheet(
        svg=sheet.render(),
        label=label,
        ground_truth=tuple(_ground_truth(label, sheet_id, grids, built.length_unit)),
    )


def _unit_scale(length_unit: str) -> float:
    """Return a PlanLabel length unit expressed in metres.

    Args:
        length_unit: ``m``, ``cm`` or ``mm``.

    Returns:
        How many metres one unit is.
    """
    return {"m": 1.0, "cm": 0.01, "mm": 0.001}[length_unit]


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


def _elements(view: RenderedView, height_mm: float, offset: tuple[float, float]) -> list[Element]:
    """Describe every product the serializer drew.

    Args:
        view: The rendered view.
        height_mm: The sheet height, for the y-flip.
        offset: The wrapper translation that placed the view.

    Returns:
        One element per product, with its paper geometry.
    """
    elements: list[Element] = []
    for index, product in enumerate(view.products):
        outlines = [
            path_points(path, page_height_mm=height_mm, offset=offset) for path in product.paths
        ]
        points = [point for outline in outlines for point in outline]
        if not points:
            continue
        elements.append(
            Element(
                id=f"e-{index:02d}",
                ifcGuid=product.guid,
                ifcClass=product.ifc_class,
                name=product.name,
                tag=f"Pos. {index + 1}",
                viewport="vp-plan",
                paperBBox=bounding_box(points),
                paperOutlines=[
                    [(point[0], point[1]) for point in outline]
                    for outline in outlines
                    if len(outline) >= _MIN_OUTLINE_POINTS
                ],
                representation="cut",
                provenance=Provenance.AUTHORED,
            )
        )
    return elements


def _draw_grids(sheet: Sheet, grids: tuple[GridAxis, ...], transform: Affine) -> list[Annotation]:
    """Draw the grid lines and their bubbles, and describe them.

    Args:
        sheet: The sheet being composed.
        grids: The grid lines.
        transform: The paper-to-plane affine.

    Returns:
        One ``grid`` annotation per line.
    """
    annotations: list[Annotation] = []
    low_x, low_y, high_x, high_y = VIEWPORT_BOX
    for grid in grids:
        if grid.vertical:
            x, _ = invert(transform, grid.position, 0.0)
            start, end = (x, low_y + 4.0), (x, high_y - 4.0)
            bubble = (x, end[1] + BUBBLE_OFFSET_MM)
        else:
            _, y = invert(transform, 0.0, grid.position)
            start, end = (low_x + 4.0, y), (high_x - 4.0, y)
            bubble = (end[0] + BUBBLE_OFFSET_MM, y)
        sheet.line(*start, *end, width=0.18)
        sheet.circle(*bubble, BUBBLE_RADIUS_MM)
        sheet.text(bubble[0], bubble[1] - 1.2, grid.axis, size=3.5, anchor="middle")
        annotations.append(
            Annotation(
                id=f"g-{grid.axis}",
                type="grid",
                viewport="vp-plan",
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


def _draw_dimensions(
    sheet: Sheet,
    grids: tuple[GridAxis, ...],
    transform: Affine,
    length_unit: str,
) -> list[Annotation]:
    """Dimension between consecutive grid lines, from the model's own spacing.

    The value is the distance the model places between the two grids, converted to
    millimetres. It is not measured off the drawing, which is what makes it authored.

    Args:
        sheet: The sheet being composed.
        grids: The grid lines.
        transform: The paper-to-plane affine.
        length_unit: The model's length unit.

    Returns:
        One ``dimension`` annotation per consecutive pair, per direction.
    """
    to_mm = _unit_scale(length_unit) * 1000.0
    annotations: list[Annotation] = []
    for vertical in (True, False):
        line = sorted(
            (grid for grid in grids if grid.vertical is vertical), key=lambda g: g.position
        )
        for first, second in pairwise(line):
            spacing = abs(second.position - first.position)
            if vertical:
                x0, _ = invert(transform, first.position, 0.0)
                x1, _ = invert(transform, second.position, 0.0)
                y = VIEWPORT_BOX[1] - 10.0
                start, end = (x0, y), (x1, y)
            else:
                _, y0 = invert(transform, 0.0, first.position)
                _, y1 = invert(transform, 0.0, second.position)
                x = VIEWPORT_BOX[0] - 10.0
                start, end = (x, y0), (x, y1)
            sheet.line(*start, *end, width=0.18)
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            printed = f"{spacing * to_mm:.0f}"
            sheet.text(midpoint[0], midpoint[1] + 1.5, printed, size=3.0, anchor="middle")
            annotations.append(
                Annotation(
                    id=f"a-dim-{first.axis}{second.axis}",
                    type="dimension",
                    viewport="vp-plan",
                    paperBBox=bounding_box([start, end, (midpoint[0], midpoint[1] + 4.0)]),
                    text=printed,
                    value=round(spacing * to_mm, 3),
                    unit="mm",
                    measures=[f"g-{first.axis}", f"g-{second.axis}"],
                    geometry=[start, end],
                    provenance=Provenance.AUTHORED,
                )
            )
    return annotations


def _draw_tags(sheet: Sheet, elements: list[Element]) -> list[Annotation]:
    """Tag each element with the mark the model holds.

    Args:
        sheet: The sheet being composed.
        elements: The elements to tag.

    Returns:
        One ``tag`` annotation per element.
    """
    annotations: list[Annotation] = []
    for element in elements:
        box = element.paper_bbox
        x = (box[0] + box[2]) / 2.0
        y = (box[1] + box[3]) / 2.0
        sheet.text(x, y, element.tag or "", size=2.5, anchor="middle")
        annotations.append(
            Annotation(
                id=f"a-tag-{element.local_id}",
                type="tag",
                viewport="vp-plan",
                paperBBox=(x - 8.0, y - 1.5, x + 8.0, y + 2.5),
                text=element.tag,
                shows=Shows(element=element.local_id, property="Tag"),
                provenance=Provenance.AUTHORED,
            )
        )
    return annotations


def _draw_callout(sheet: Sheet, target_sheet: str) -> Annotation:
    """Draw a callout pointing at another sheet.

    Args:
        sheet: The sheet being composed.
        target_sheet: The sheet id it refers to.

    Returns:
        The ``callout`` annotation.
    """
    centre = (VIEWPORT_BOX[2] - 20.0, VIEWPORT_BOX[1] + 20.0)
    sheet.circle(*centre, 6.0)
    sheet.text(centre[0], centre[1] - 1.2, target_sheet, size=2.5, anchor="middle")
    return Annotation(
        id="a-call-01",
        type="callout",
        viewport="vp-plan",
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


def _ground_truth(
    label: PageLabel, sheet_id: str, grids: tuple[GridAxis, ...], length_unit: str
) -> list[dict[str, object]]:
    """Derive questions whose answers come from the model, not from the drawing.

    Args:
        label: The page label.
        sheet_id: The sheet number.
        grids: The grid lines.
        length_unit: The model's length unit.

    Returns:
        One question per fact worth asking about.
    """
    questions: list[dict[str, object]] = [
        {
            "sheet": sheet_id,
            "category": "count",
            "question": f"How many IfcWall elements are shown on sheet {sheet_id}?",
            "answer": sum(1 for e in label.elements or [] if e.ifc_class == "IfcWall"),
        },
        {
            "sheet": sheet_id,
            "category": "sheet",
            "question": f"What is the drawing scale of sheet {sheet_id}?",
            "answer": label.sheet.scale,
        },
        {
            "sheet": sheet_id,
            "category": "level",
            "question": f"What conformance level does the label of sheet {sheet_id} reach?",
            "answer": conformance_level(label).value,
        },
    ]
    for annotation in label.annotations or []:
        if annotation.annotation_type == "dimension" and annotation.value is not None:
            first, second = (annotation.measures or ["?", "?"])[:2]
            questions.append(
                {
                    "sheet": sheet_id,
                    "category": "dimension",
                    "question": (
                        f"On sheet {sheet_id}, what is the distance in millimetres "
                        f"between grid {first.removeprefix('g-')} and grid "
                        f"{second.removeprefix('g-')}?"
                    ),
                    "answer": annotation.value,
                    "unit": "mm",
                }
            )
        if annotation.annotation_type == "callout" and annotation.target is not None:
            questions.append(
                {
                    "sheet": sheet_id,
                    "category": "callout",
                    "question": f"Which sheet does the callout on {sheet_id} refer to?",
                    "answer": annotation.target.sheet_id,
                }
            )
    questions.append(
        {
            "sheet": sheet_id,
            "category": "grid",
            "question": f"Which grid axes are drawn on sheet {sheet_id}?",
            "answer": sorted(grid.axis for grid in grids),
        }
    )
    questions.append(
        {
            "sheet": sheet_id,
            "category": "model",
            "question": f"What length unit does the model behind sheet {sheet_id} use?",
            "answer": length_unit,
        }
    )
    return questions


def write_sample(
    exported: ExportedSheet,
    built: BuiltModel,
    out_dir: Path,
    *,
    mod_date: datetime,
) -> Path:
    """Write one complete sample set.

    Args:
        exported: The composed sheet and its label.
        built: The model it was drawn from.
        out_dir: The directory to write into, which is created.
        mod_date: The timestamp to stamp, so the output is reproducible.

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
        exported.svg, out_dir / "sheet.pdf", width_mm=width, height_mm=height, mod_date=mod_date
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
