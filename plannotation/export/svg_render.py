# SPDX-License-Identifier: Apache-2.0
"""Render an IFC model to SVG with the IfcOpenShell serializer.

The settings here are not defaults and are not arbitrary. A serializer left to its own
devices on a model built through ``ifcopenshell.api`` writes only its CSS scaffolding
and no geometry at all, which looks like success until you open the file. Four settings
between them decide whether a drawing appears:

``iterator-output = NATIVE``
    The serializer wants native geometry, not triangles. With triangulated output it
    receives shapes it cannot section and silently draws nothing.
``setPolygonal(True)`` and ``setProfileThreshold(-1)``
    Emit polygons rather than curve approximations, and never fall back to a profile.
``setAlwaysProject(True)``
    Draw what is below the cut as a projection, so a plan is a plan and not an outline.
``setSectionHeight(...)``
    Where the cut is. A model whose storeys carry no elevation gives
    ``setSectionHeightsFromStoreys`` nothing to work from, so the height is passed
    explicitly and recorded in the plannotation as the viewport's ``cutHeight``.

What comes out carries the conventions SPEC 6.4.1 says Plannotation must not disturb:
a product group is ``id="product-<uuid>-body"`` with ``class="IfcWall"``,
``ifc:guid`` holding the IFC GlobalId and ``ifc:name`` the product's name, and the view
group carries ``ifc:matrix3`` and ``ifc:plane``. Note that the GlobalId is in
``ifc:guid`` and **not** in the id, which is a fresh UUID -- reading the id would give
an identifier that means nothing outside this one file.

ifcopenshell lives behind the ``ifc`` extra and is not installed in CI, so it is
imported inside the functions that need it.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from plannotation.errors import MissingExtraError

if TYPE_CHECKING:
    from pathlib import Path

    from plannotation.export.models import SectionCut

#: Products never drawn: an opening is a void, and drawing it fills the hole it makes.
EXCLUDED_CLASSES = ("IfcOpeningElement",)


@dataclass(frozen=True)
class DrawnProduct:
    """One product as the serializer drew it.

    Attributes:
        guid: The IFC GlobalId, read from ``ifc:guid``.
        ifc_class: The IFC class, read from ``class``.
        name: The product's name, read from ``ifc:name``, or None.
        paths: Every ``d`` attribute the product's group carries.

    Note:
        Whether a product is *cut* or *projected* is deliberately not recorded here.
        With ``setAlwaysProject`` the serializer merges both into one path per product
        and marks neither, so the SVG does not know. The exporter decides it from the
        model instead -- a product is cut when its own vertical extent contains the
        section height -- which is where the answer actually lives. Guessing it from
        the drawing would put a confident value in the plannotation that nothing
        supports.
    """

    guid: str
    ifc_class: str
    name: str | None
    paths: tuple[str, ...]


@dataclass(frozen=True)
class RenderedView:
    """One rendered view and everything a plannotation needs from it.

    Attributes:
        svg: The serializer's SVG text.
        matrix3: The view group's ``ifc:matrix3``, plane metres to SVG user units.
        ifc_plane: The view group's ``ifc:plane``, as JSON text.
        products: Every product drawn, in the order the serializer emitted them.
        width_mm: The bounding rectangle's width.
        height_mm: Its height.
    """

    svg: str
    matrix3: tuple[tuple[float, float, float], ...]
    ifc_plane: str
    products: tuple[DrawnProduct, ...]
    width_mm: float
    height_mm: float


def _require(name: str) -> Any:  # noqa: ANN401 - ifcopenshell is untyped here
    """Import one ifcopenshell module, or say what to install.

    Args:
        name: The module to import.

    Returns:
        The module.

    Raises:
        MissingExtraError: If it is not installed.
    """
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        msg = (
            f"the authored exporter needs {name.partition('.')[0]}, which is not installed. "
            f"It lives behind an optional extra: install it with "
            f"`pip install 'plannotation[ifc]'` or `uv sync --extra ifc`"
        )
        raise MissingExtraError(msg) from exc


def render_view(
    model_path: Path,
    *,
    scale_denominator: float,
    width_mm: float,
    height_mm: float,
    section_height: float | None = None,
    section: SectionCut | None = None,
    name: str = "",
) -> RenderedView:
    """Render one view of a model to SVG: a plan cut, or a vertical section.

    Args:
        model_path: The IFC file.
        scale_denominator: The drawing scale's denominator, so 50 for 1:50.
        width_mm: The bounding rectangle's width in millimetres.
        height_mm: Its height in millimetres.
        section_height: For a plan, where to cut, in metres above the storey.
        section: For a section, the vertical cutting plane. The serializer draws it
            through ``addDrawing`` with the storeys' own plans switched off.
        name: The view's name, recorded as the view group's ``ifc:name``.

    Returns:
        The rendered view.

    Raises:
        MissingExtraError: If ifcopenshell is not installed.
        ValueError: If the serializer produced no view group, which means it drew
            nothing and the drawing would be empty.
    """
    ifcopenshell = _require("ifcopenshell")
    geom = _require("ifcopenshell.geom")
    wrapper = ifcopenshell.ifcopenshell_wrapper

    model = ifcopenshell.open(str(model_path))
    settings = geom.settings(ELEMENT_HIERARCHY=True)
    settings.set("dimensionality", wrapper.SURFACES_AND_SOLIDS)
    settings.set("iterator-output", wrapper.NATIVE)
    settings.set("apply-default-materials", True)  # noqa: FBT003 - the wrapper is positional

    buffer = geom.serializers.buffer()
    serializer = geom.serializers.svg(buffer, settings, geom.serializer_settings())
    serializer.setFile(model)
    if section is None:
        serializer.setSectionHeight(1.2 if section_height is None else section_height)
    else:
        serializer.setWithoutStoreys(True)
        serializer.addDrawing(section.location, section.direction, section.x_axis, name, True)  # noqa: FBT003
    serializer.setPolygonal(True)
    serializer.setUseNamespace(True)
    serializer.setAlwaysProject(True)
    serializer.setProfileThreshold(-1)
    serializer.setBoundingRectangle(width_mm, height_mm)
    serializer.setScale(1.0 / scale_denominator)
    serializer.setAutoElevation(False)
    serializer.setAutoSection(False)
    serializer.setDrawDoorArcs(False)
    serializer.setPrintSpaceNames(False)
    serializer.setNoCSS(False)
    serializer.setUseHlrPoly(False)
    serializer.setUsePrefiltering(True)
    serializer.setUnifyInputs(True)
    serializer.setMirrorY(False)
    serializer.setDrawStoreyHeights(0)

    iterator = geom.iterator(settings, model, exclude=list(EXCLUDED_CLASSES))
    if iterator.initialize():
        while True:
            serializer.write(iterator.get())
            if not iterator.next():
                break
    serializer.finalize()
    svg = buffer.get_value()

    matrix3, ifc_plane = _read_view_group(svg)
    return RenderedView(
        svg=svg,
        matrix3=matrix3,
        ifc_plane=ifc_plane,
        products=tuple(read_products(svg)),
        width_mm=width_mm,
        height_mm=height_mm,
    )


def _read_view_group(svg: str) -> tuple[tuple[tuple[float, float, float], ...], str]:
    """Read the view group's transform attributes.

    Args:
        svg: The serializer's output.

    Returns:
        The ``ifc:matrix3`` rows and the ``ifc:plane`` JSON text.

    Raises:
        ValueError: If no view group is present, which means nothing was drawn.
    """
    matrix = re.search(r'ifc:matrix3="([^"]+)"', svg)
    plane = re.search(r'ifc:plane="([^"]+)"', svg)
    if matrix is None or plane is None:
        msg = (
            "the serializer produced no view group, which means it drew nothing. The "
            "usual cause is a section height that does not cut the geometry, or "
            "iterator-output left at its default rather than NATIVE"
        )
        raise ValueError(msg)
    rows = json.loads(matrix.group(1))
    shaped = tuple((float(row[0]), float(row[1]), float(row[2])) for row in rows[:3])
    return shaped, plane.group(1)


def read_products(svg: str) -> list[DrawnProduct]:
    """Read every product group out of a rendered view.

    The GlobalId is taken from ``ifc:guid`` rather than from the element id. The id is a
    fresh UUID that identifies the shape within this one file and means nothing outside
    it; the guid is the identity the model and the plannotation share.

    Groups nest -- every product sits inside the view group -- so the extent of a group
    is found by counting opening and closing tags rather than by matching to the next
    ``</g>``. A non-greedy match stops at the first close, which for nested groups means
    the outer group swallows the first product and it silently disappears from the
    plannotation. That is exactly the failure this function was written with and it
    cost a wall.

    Args:
        svg: The serializer's output.

    Returns:
        One entry per product group, in document order.
    """
    products: list[DrawnProduct] = []
    for match in re.finditer(r"<g\b([^>]*)>", svg):
        attributes = match.group(1)
        guid = re.search(r'ifc:guid="([^"]+)"', attributes)
        ifc_class = re.search(r'class="([^"]+)"', attributes)
        if guid is None or ifc_class is None or not guid.group(1):
            continue
        body = svg[match.end() : _group_end(svg, match.end())]
        name = re.search(r'ifc:name="([^"]*)"', attributes)
        classes = ifc_class.group(1).split()
        products.append(
            DrawnProduct(
                guid=guid.group(1),
                ifc_class=next((token for token in classes if token.startswith("Ifc")), classes[0]),
                name=name.group(1) if name else None,
                paths=tuple(re.findall(r'\bd="([^"]+)"', body)),
            )
        )
    return products


def _group_end(svg: str, start: int) -> int:
    """Return the offset of the close tag matching a group opened before ``start``.

    Args:
        svg: The document.
        start: The offset just past the group's opening tag.

    Returns:
        The offset where the group's content ends.
    """
    depth = 1
    position = start
    for token in re.finditer(r"<g\b[^>]*>|</g>", svg[start:]):
        depth += 1 if token.group(0).startswith("<g") else -1
        if depth == 0:
            return start + token.start()
        position = start + token.end()
    return position
