# SPDX-License-Identifier: Apache-2.0
"""Build a page label from a sheet SVG, and attach it to the PDF drawn from that SVG.

This is the path for authoring tools that draw through IfcOpenShell's serializer --
Bonsai among them. The tool already wrote the SVG; its PDF is a rendering of it. The
SVG carries every product's GlobalId and class and every view's paper-to-model
transform (:mod:`plannotation.svg.carrier`), so a label written from it is ``authored``:
it states what the authoring tool drew, not what someone read off the page.

The label reaches conformance level L2 -- sheet, viewports and elements. Annotations
(dimensions, tags, grids) are not derived: the serializer does not mark them, and
guessing them from line work is inference's job, not a carrier's.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import pikepdf

from plannotation import __version__
from plannotation.constants import SCHEMA_VERSION
from plannotation.errors import CarrierError, LabelMismatchError
from plannotation.export.geometry import union_box
from plannotation.export.paper import Affine, plane_from_ifc_plane, scale_denominator
from plannotation.model import (
    DrawingType,
    Element,
    Generator,
    LengthUnit,
    Model,
    Page,
    PageLabel,
    Plane,
    Provenance,
    Viewport,
    ViewportKind,
)
from plannotation.model import Sheet as SheetInfo
from plannotation.pdf import embed
from plannotation.svg.carrier import SvgSheet, SvgView, compose, read_svg
from plannotation.units import length_unit_for

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

from pathlib import Path

logger = logging.getLogger(__name__)

#: How far the PDF page may differ from the SVG page and still be its rendering, in
#: millimetres: the geometric tolerance of SPEC 3.1.
PAGE_SIZE_TOLERANCE_MM: Final = 0.5

#: Products an SVG may name that are not building elements drawn on a sheet: the
#: spatial structure, and the annotation and grid entities a label describes as
#: annotations rather than elements.
NOT_ELEMENTS: Final = frozenset(
    {
        "IfcAnnotation",
        "IfcGrid",
        "IfcGridAxis",
        "IfcProject",
        "IfcSite",
        "IfcBuilding",
        "IfcBuildingStorey",
    }
)

#: How far off horizontal a view's axes may be for it to be a plan.
_HORIZONTAL: Final = 1e-6

#: How close to a whole number a derived scale must be to be reported as one.
_WHOLE: Final = 1e-6


@dataclass(frozen=True)
class SheetSource:
    """What the SVG does not say, and the authoring tool knows.

    Attributes:
        sheet_id: The sheet number, such as ``A-101``.
        unit_scale_to_m: The model's length unit in metres, as
            ``ifcopenshell.util.unit.calculate_unit_scale`` reports it.
        length_unit: The same unit by name: ``m``, ``cm`` or ``mm``.
        title: The sheet's title.
        tags: Each product's ``Tag`` by GlobalId, when the model is at hand.
        model_file: The IFC file's name.
        ifc_schema: The IFC schema, such as ``IFC4``.
    """

    sheet_id: str
    unit_scale_to_m: float
    length_unit: LengthUnit
    title: str | None = None
    tags: Mapping[str, str] | None = None
    model_file: str | None = None
    ifc_schema: str | None = None


def derive_label(sheet: SvgSheet, source: SheetSource, *, page_index: int = 0) -> PageLabel:
    """Describe a sheet SVG as a page label.

    Args:
        sheet: What :func:`plannotation.svg.carrier.read_svg` read.
        source: What the authoring tool knows that the SVG does not.
        page_index: The page of the PDF the label is for.

    Returns:
        An ``authored`` label at conformance level L2, or L1 when the sheet draws no
        IFC product.
    """
    tags = source.tags or {}
    elements: list[Element] = []
    drawn = [p for p in sheet.products if p.ifc_class not in NOT_ELEMENTS]
    width = max(2, len(str(len(drawn) - 1)))
    for index, product in enumerate(drawn):
        elements.append(
            Element(
                id=f"e-{index:0{width}d}",
                ifcGuid=product.guid,
                ifcClass=product.ifc_class,
                name=product.name,
                tag=tags.get(product.guid),
                viewport=None if product.view is None else _viewport_id(product.view),
                paperBBox=product.paper_bbox,
                paperOutlines=[list(outline) for outline in product.outlines if len(outline) > 1],
            )
        )
    viewports = [
        _viewport(view, sheet, source, elements)
        for view in sheet.views
        if any(element.viewport == _viewport_id(view.index) for element in elements)
    ]
    scales = {viewport.scale for viewport in viewports}
    return PageLabel(
        plannotation=SCHEMA_VERSION,
        generator=Generator(name="plannotation", version=__version__),
        provenance=Provenance.AUTHORED,
        page=Page(index=page_index, widthMm=sheet.width_mm, heightMm=sheet.height_mm),
        sheet=SheetInfo(
            id=source.sheet_id,
            title=source.title,
            scale=scales.pop() if len(scales) == 1 else None,
            drawingType=_drawing_type(viewports),
        ),
        model=Model(
            file=source.model_file, lengthUnit=source.length_unit, schema=source.ifc_schema
        ),
        viewports=viewports or None,
        elements=elements or None,
    )


def source_from_model(
    model: Any,  # noqa: ANN401 -- an ifcopenshell.file, untyped
    *,
    sheet_id: str,
    title: str | None = None,
    model_file: str | None = None,
) -> SheetSource:
    """Take what an SVG does not say from the model it was drawn from.

    Args:
        model: The open IFC model.
        sheet_id: The sheet number.
        title: The sheet title.
        model_file: The model's file name.

    Returns:
        The source: units, schema, and every product's ``Tag``.

    Raises:
        ValueError: If the model's length unit is not one Plannotation names.
    """
    unit = importlib.import_module("ifcopenshell.util.unit")
    scale = float(unit.calculate_unit_scale(model))
    tags = {
        str(product.GlobalId): str(product.Tag)
        for product in model.by_type("IfcProduct")
        if getattr(product, "Tag", None)
    }
    return SheetSource(
        sheet_id=sheet_id,
        unit_scale_to_m=scale,
        length_unit=length_unit_for(scale),
        title=title,
        tags=tags,
        model_file=model_file,
        ifc_schema=str(model.schema),
    )


def source_from_ifc(path: Path, *, sheet_id: str, title: str | None = None) -> SheetSource:
    """Open an IFC file and take the sheet's source from it.

    Args:
        path: The IFC file.
        sheet_id: The sheet number.
        title: The sheet title.

    Returns:
        The source.

    Raises:
        CarrierError: If the ``ifc`` extra is not installed.
    """
    try:
        ifcopenshell = importlib.import_module("ifcopenshell")
    except ImportError as error:
        msg = "reading the model needs the ifc extra: pip install 'plannotation[ifc]'"
        raise CarrierError(msg) from error
    model = ifcopenshell.open(str(path))
    return source_from_model(model, sheet_id=sheet_id, title=title, model_file=Path(path).name)


def attach_from_svg(
    svg: Path,
    pdf_in: Path,
    pdf_out: Path,
    source: SheetSource,
    *,
    mod_date: datetime,
) -> PageLabel:
    """Label a one-sheet PDF from the SVG it was drawn from.

    Args:
        svg: The sheet SVG.
        pdf_in: The PDF rendered from it. Its first page is the sheet.
        pdf_out: Where to write the labelled copy; ``pdf_in`` is not modified.
        source: What the authoring tool knows that the SVG does not.
        mod_date: The modification date to stamp, so the output is reproducible.

    Returns:
        The label that was attached.

    Raises:
        LabelMismatchError: If the PDF's first page is not the SVG's page size, which
            means it is not a rendering of this SVG and the label would misplace
            everything on it.
    """
    sheet = read_svg(svg)
    with pikepdf.open(pdf_in) as pdf:
        geometry = embed.page_geometry(pdf, 0)
    if (
        abs(geometry.width_mm - sheet.width_mm) > PAGE_SIZE_TOLERANCE_MM
        or abs(geometry.height_mm - sheet.height_mm) > PAGE_SIZE_TOLERANCE_MM
    ):
        msg = (
            f"{pdf_in.name} page 1 is {geometry.width_mm:.1f} x {geometry.height_mm:.1f} mm "
            f"but {svg.name} is {sheet.width_mm:.1f} x {sheet.height_mm:.1f} mm: the PDF is "
            "not a rendering of this SVG"
        )
        raise LabelMismatchError(msg)
    label = derive_label(sheet, source)
    embed.attach(pdf_in, [label], embed.build_index([label]), pdf_out, mod_date=mod_date)
    return label


def paper_to_plane(view: SvgView, height_mm: float, unit_scale_to_m: float) -> Affine:
    """Return the SPEC 3.5 ``paperToPlane`` of a view placed anywhere on a sheet.

    The serializer's ``ifc:matrix3`` maps the view plane, in metres and with y negated,
    to the view group's user units; the view's placement takes those to page
    millimetres, y down; the flip of SPEC 3.3 takes them to paper. That chain is
    inverted, and its output divided into the model's own length unit.

    Args:
        view: The view.
        height_mm: The page height.
        unit_scale_to_m: The model's length unit in metres.

    Returns:
        ``(a, b, c, d, e, f)``, taking paper millimetres to the model plane.

    Raises:
        ValueError: If the chain is degenerate, which would draw the view as a line.
    """
    (m00, m01, m02), (m10, m11, m12) = view.matrix3
    plane_to_view = (m00, m10, -m01, -m11, m02, m12)
    flip = (1.0, 0.0, 0.0, -1.0, 0.0, height_mm)
    a, b, c, d, e, f = compose(flip, compose(view.placement, plane_to_view))
    determinant = a * d - b * c
    if determinant == 0:
        msg = "the view's placement is degenerate: it would draw the view as a line"
        raise ValueError(msg)
    inverse = (
        d / determinant,
        -b / determinant,
        -c / determinant,
        a / determinant,
        (c * f - d * e) / determinant,
        (b * e - a * f) / determinant,
    )
    scale = unit_scale_to_m
    return (
        inverse[0] / scale,
        inverse[1] / scale,
        inverse[2] / scale,
        inverse[3] / scale,
        inverse[4] / scale,
        inverse[5] / scale,
    )


def _viewport(
    view: SvgView, sheet: SvgSheet, source: SheetSource, elements: list[Element]
) -> Viewport:
    """Describe one view as a viewport.

    Args:
        view: The view.
        sheet: The sheet it is on.
        source: The model's units.
        elements: Every element, some of them in this view.

    Returns:
        The viewport, sized to the elements drawn in it.
    """
    identifier = _viewport_id(view.index)
    transform = paper_to_plane(view, sheet.height_mm, source.unit_scale_to_m)
    denominator = scale_denominator(transform, source.unit_scale_to_m)
    if abs(denominator - round(denominator)) < _WHOLE * denominator:
        denominator = float(round(denominator))
    plane = None
    kind: ViewportKind = "plan"
    if view.plane:
        origin, x_axis, y_axis = plane_from_ifc_plane(view.plane, source.unit_scale_to_m)
        plane = Plane(
            origin=(origin[0], origin[1], origin[2]),
            xAxis=(x_axis[0], x_axis[1], x_axis[2]),
            yAxis=(y_axis[0], y_axis[1], y_axis[2]),
        )
        if abs(x_axis[2]) > _HORIZONTAL or abs(y_axis[2]) > _HORIZONTAL:
            kind = "section"
    return Viewport(
        id=identifier,
        name=view.name,
        kind=kind,
        scale=denominator,
        paperBBox=union_box(e.paper_bbox for e in elements if e.viewport == identifier),
        plane=plane,
        paperToPlane=transform,
    )


def _viewport_id(index: int) -> str:
    """Return the local id of the viewport for a view.

    Args:
        index: The view's index in the document.

    Returns:
        ``vp-01`` for the first view, and so on.
    """
    return f"vp-{index + 1:02d}"


def _drawing_type(viewports: list[Viewport]) -> DrawingType | None:
    """Say what kind of drawing a sheet is, when its viewports agree.

    Args:
        viewports: The sheet's viewports.

    Returns:
        ``plan`` or ``section`` when every viewport is one, else None.
    """
    kinds = {viewport.kind for viewport in viewports}
    if kinds == {"plan"}:
        return "plan"
    if kinds == {"section"}:
        return "section"
    return None
