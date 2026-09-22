# SPDX-License-Identifier: Apache-2.0
"""Read the IFC identity an IfcOpenShell SVG already carries, in paper millimetres.

PlanLabel 0.1 reserves the SVG *payload* (SPEC 6.4.4): no PlanLabel data is written
into an SVG, and nothing here writes one. What an SVG does carry already is enough to
build a label from. IfcOpenShell's serializer -- and Bonsai, which draws with it --
marks every product group with its GlobalId (``ifc:guid``, or ``id="product-<uuid>"``)
and its IFC class (``class``), and every view group with ``ifc:matrix3`` and
``ifc:plane``, which together say how the view's paper maps to the model.

This module reads those markers, never renames or rewrites them (SPEC 6.4.1), and
returns the geometry in the coordinates a label uses: millimetres, origin bottom-left,
y up (SPEC 3.1). Units, ``viewBox``, nested ``<svg>`` viewports and every transform are
resolved on the way, so the flip of SPEC 3.3 happens in exactly one place.

The document is untrusted input (SPEC 9.3). A document type declaration is refused
outright -- entity expansion is how XML parsers are attacked, and no drawing needs one
-- the size and the number of elements are bounded, and the tree is walked without
recursion. An ``<image>`` that references another SVG, which is how a Bonsai sheet
layout places its drawings, is followed only to a relative local path and only two
levels deep.
"""

from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from svgelements import Path as SvgPath

from planlabel.errors import CarrierError
from planlabel.export.geometry import bounding_box, segment_points
from planlabel.units import COORD_DECIMALS

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

IFC_NAMESPACE: Final = "http://www.ifcopenshell.org/ns"
SVG_NAMESPACE: Final = "http://www.w3.org/2000/svg"
_SVG: Final = f"{{{SVG_NAMESPACE}}}"
_IFC: Final = f"{{{IFC_NAMESPACE}}}"
_XLINK_HREF: Final = "{http://www.w3.org/1999/xlink}href"

#: The largest SVG read, in bytes. A composed A0 sheet is a few megabytes.
MAX_SVG_BYTES: Final = 64 * 1024 * 1024

#: The most elements walked in one document, referenced drawings included.
MAX_ELEMENTS: Final = 2_000_000

#: How deep ``<image href="drawing.svg">`` references are followed.
MAX_IMAGE_DEPTH: Final = 2

#: Sides used to flatten a circle or an ellipse.
_ELLIPSE_SIDES: Final = 32

#: IFC's base64 alphabet, in the order a GlobalId's digits are valued.
GUID_ALPHABET: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
_GUID_LENGTH: Final = 22
_GUID: Final = re.compile(r"[0-3][0-9A-Za-z_$]{21}")
_UUID: Final = re.compile(r"[0-9a-fA-F]{8}-?(?:[0-9a-fA-F]{4}-?){3}[0-9a-fA-F]{12}")
_IFC_CLASS: Final = re.compile(r"Ifc[A-Z][A-Za-z0-9]*")
_NUMBER: Final = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_LENGTH: Final = re.compile(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)\s*")
_TRANSFORM: Final = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")

#: Millimetres per CSS unit. A bare number is a CSS pixel, 1/96 in.
_MM_PER_UNIT: Final = {
    "": 25.4 / 96.0,
    "px": 25.4 / 96.0,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "in": 25.4,
    "cm": 10.0,
    "mm": 1.0,
    "q": 0.25,
}

#: Elements whose content is never drawn where it stands.
_NOT_RENDERED: Final = frozenset(
    f"{_SVG}{name}"
    for name in (
        "defs",
        "symbol",
        "clipPath",
        "mask",
        "pattern",
        "marker",
        "metadata",
        "title",
        "desc",
        "style",
        "script",
        "linearGradient",
        "radialGradient",
    )
)

#: An SVG transform ``(a, b, c, d, e, f)``: ``x' = a x + c y + e``, ``y' = b x + d y + f``.
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Final[Matrix] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

Point = tuple[float, float]
Outline = tuple[Point, ...]
Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True)
class SvgView:
    """One view group: a drawing the serializer projected, placed on the sheet.

    Attributes:
        index: Its position among the document's views.
        name: Its ``ifc:name``, when it has one.
        matrix3: The top two rows of ``ifc:matrix3``, which maps the view plane in
            metres -- with its y negated, as the serializer projects -- to the group's
            own user units.
        plane: The raw ``ifc:plane`` text, a 4x4 placing the view in the model.
        placement: The group's user units to page millimetres, y down: every
            transform and viewport above it, composed.
    """

    index: int
    name: str | None
    matrix3: Matrix3
    plane: str | None
    placement: Matrix


@dataclass(frozen=True)
class SvgProduct:
    """One IFC product drawn on the sheet, in one view.

    Attributes:
        guid: Its IFC GlobalId.
        ifc_class: Its IFC class, as the ``class`` attribute gives it.
        name: Its ``ifc:name``, when it is not empty.
        view: The index of the view it is drawn in, or None when it sits in none.
        outlines: What it draws, in paper millimetres, y up.
    """

    guid: str
    ifc_class: str
    name: str | None
    view: int | None
    outlines: tuple[Outline, ...]

    @property
    def paper_bbox(self) -> tuple[float, float, float, float]:
        """Return the box around everything the product draws."""
        return bounding_box(point for outline in self.outlines for point in outline)


@dataclass(frozen=True)
class SvgSheet:
    """What one SVG says about the IFC products on it.

    Attributes:
        width_mm: The page's width in millimetres.
        height_mm: Its height.
        views: Every view group, in document order.
        products: Every product with geometry, one per view it appears in.
    """

    width_mm: float
    height_mm: float
    views: tuple[SvgView, ...]
    products: tuple[SvgProduct, ...]


@dataclass
class _Product:
    """A product being collected: identity fixed, outlines growing."""

    guid: str
    ifc_class: str
    name: str | None
    view: int | None
    outlines: list[Outline] = field(default_factory=list)


@dataclass(frozen=True)
class _Frame:
    """One element waiting to be walked, with everything inherited from above it.

    Attributes:
        element: The element.
        matrix: Its parent's user units to page millimetres, y down.
        view: The view it sits in, if any.
        product: The product it draws part of, if any.
        base: The directory its ``<image>`` references resolve against.
        image_depth: How many ``<image>`` references were followed to reach it.
    """

    element: ET.Element
    matrix: Matrix
    view: int | None
    product: _Product | None
    base: Path | None
    image_depth: int


def read_svg(source: Path | str) -> SvgSheet:
    """Read an SVG file.

    Args:
        source: The file. ``<image>`` references to other SVGs are resolved relative
            to its directory.

    Returns:
        The page size, the views and the products.

    Raises:
        CarrierError: If the file is too large, declares a DTD, or is not an SVG.
    """
    path = Path(source)
    return parse_svg(_read_bounded(path), base=path.parent)


def parse_svg(data: bytes, *, base: Path | None = None) -> SvgSheet:
    """Read an SVG document already in memory.

    Args:
        data: The document.
        base: The directory ``<image>`` references resolve against; None follows none.

    Returns:
        The page size, the views and the products.

    Raises:
        CarrierError: If the document is too large, declares a DTD, is not
            well-formed, is not an SVG, or its page size cannot be determined.
    """
    root = _parse_root(data)
    width_mm, height_mm, placement = _root_viewport(root)
    walker = _Walker(height_mm)
    walker.walk(root, placement, base)
    return SvgSheet(width_mm, height_mm, tuple(walker.views), walker.products())


class _Walker:
    """Walks a document once, collecting views and products as it meets them."""

    def __init__(self, height_mm: float) -> None:
        """Start an empty walk.

        Args:
            height_mm: The page height, for the y-flip.
        """
        self.height_mm = height_mm
        self.views: list[SvgView] = []
        self._products: dict[tuple[int | None, str], _Product] = {}
        self._walked = 0

    def products(self) -> tuple[SvgProduct, ...]:
        """Return every product that drew something, in the order first met.

        Returns:
            The products.
        """
        found = []
        for product in self._products.values():
            if not product.outlines:
                logger.debug("%s %s draws nothing", product.ifc_class, product.guid)
                continue
            found.append(
                SvgProduct(
                    product.guid,
                    product.ifc_class,
                    product.name,
                    product.view,
                    tuple(product.outlines),
                )
            )
        return tuple(found)

    def walk(self, root: ET.Element, placement: Matrix, base: Path | None) -> None:
        """Walk a document's content, depth first, without recursion.

        Args:
            root: The document's ``<svg>``; its own viewport is ``placement``.
            placement: Its user units to page millimetres, y down.
            base: The directory ``<image>`` references resolve against.

        Raises:
            CarrierError: If the document has more elements than :data:`MAX_ELEMENTS`.
        """
        stack = [_Frame(child, placement, None, None, base, 0) for child in reversed(root)]
        while stack:
            frame = stack.pop()
            self._walked += 1
            if self._walked > MAX_ELEMENTS:
                msg = f"the SVG has more than {MAX_ELEMENTS} elements; refusing to read it"
                raise CarrierError(msg)
            stack.extend(reversed(self._visit(frame)))

    def _visit(self, frame: _Frame) -> list[_Frame]:
        """Read one element and return its children, ready to walk.

        Args:
            frame: The element and what it inherits.

        Returns:
            The frames of its children, in document order.
        """
        element = frame.element
        name = _local(element.tag)
        if name is None or f"{_SVG}{name}" in _NOT_RENDERED:
            return []
        matrix = compose(frame.matrix, parse_transform(element.get("transform", "")))
        if name == "svg":
            matrix = compose(matrix, _nested_viewport(element))
        elif name == "image":
            return self._image(frame, matrix)
        view = frame.view
        if element.get(f"{_IFC}matrix3") is not None:
            view = self._view(element, matrix, view)
        product = frame.product
        guid = product_guid(element)
        if guid is not None:
            product = self._product(guid, element, view) or product
        if product is not None:
            outline = _outline(name, element, matrix, self.height_mm)
            if outline:
                product.outlines.append(outline)
        return [
            _Frame(child, matrix, view, product, frame.base, frame.image_depth) for child in element
        ]

    def _view(self, element: ET.Element, matrix: Matrix, current: int | None) -> int | None:
        """Record a view group.

        Args:
            element: The group carrying ``ifc:matrix3``.
            matrix: Its user units to page millimetres.
            current: The view it is nested in, kept if this one cannot be read.

        Returns:
            The index of the view its content belongs to.
        """
        matrix3 = _matrix3(element.get(f"{_IFC}matrix3", ""))
        if matrix3 is None:
            logger.warning("a view group's ifc:matrix3 is not a 3x3 matrix; it is ignored")
            return current
        index = len(self.views)
        self.views.append(
            SvgView(
                index=index,
                name=element.get(f"{_IFC}name") or None,
                matrix3=matrix3,
                plane=element.get(f"{_IFC}plane"),
                placement=matrix,
            )
        )
        return index

    def _product(self, guid: str, element: ET.Element, view: int | None) -> _Product | None:
        """Start, or continue, collecting one product in one view.

        A product the serializer draws in several groups -- its body, its axis --
        appears once per view, with every group's outlines.

        Args:
            guid: Its GlobalId.
            element: The group that names it.
            view: The view it is drawn in.

        Returns:
            The product being collected, or None when the group names no IFC class.
        """
        ifc_class = ifc_class_of(element)
        if ifc_class is None:
            logger.debug("product %s has no IFC class on its group; it is ignored", guid)
            return None
        key = (view, guid)
        if key not in self._products:
            name = element.get(f"{_IFC}name") or None
            self._products[key] = _Product(guid, ifc_class, name, view)
        return self._products[key]

    def _image(self, frame: _Frame, matrix: Matrix) -> list[_Frame]:
        """Follow an ``<image>`` that places another SVG, as a sheet layout does.

        Args:
            frame: The image element and what it inherits.
            matrix: Its user units to page millimetres.

        Returns:
            The referenced document's content, placed where the image is; nothing when
            the reference is not a relative local SVG, or is too deep.
        """
        element = frame.element
        href = element.get("href") or element.get(_XLINK_HREF) or ""
        target = _local_reference(href, frame.base)
        if target is None:
            return []
        if frame.image_depth >= MAX_IMAGE_DEPTH:
            logger.warning("not following %s: images nest deeper than %d", href, MAX_IMAGE_DEPTH)
            return []
        child = _parse_root(_read_bounded(target))
        box = _box(element, child)
        if box is None:
            return []
        placed = compose(
            matrix, _fit(_view_box(child) or box[2:], box, element.get("preserveAspectRatio"))
        )
        return [
            _Frame(
                grandchild, placed, frame.view, frame.product, target.parent, frame.image_depth + 1
            )
            for grandchild in child
        ]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def guid_from_uuid(text: str) -> str:
    """Turn a UUID into the 22-character IFC GlobalId that encodes it.

    Args:
        text: The UUID, with or without hyphens.

    Returns:
        The GlobalId.

    Raises:
        ValueError: If ``text`` is not 32 hexadecimal digits.

    Examples:
        >>> guid_from_uuid("00000000-0000-0000-0000-000000000000")
        '0000000000000000000000'
    """
    digits = text.replace("-", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", digits):
        msg = f"not a UUID: {text!r}"
        raise ValueError(msg)
    value = int(digits, 16)
    characters = []
    for _ in range(_GUID_LENGTH):
        value, remainder = divmod(value, 64)
        characters.append(GUID_ALPHABET[remainder])
    return "".join(reversed(characters))


def uuid_from_guid(guid: str) -> str:
    """Turn a 22-character IFC GlobalId back into the UUID it encodes.

    The inverse of :func:`guid_from_uuid`, and what the serializer writes into a
    product group's ``id="product-<uuid>"``.

    Args:
        guid: The GlobalId.

    Returns:
        The UUID, lower case, hyphenated.

    Raises:
        ValueError: If ``guid`` is not a GlobalId: 22 characters of IFC's alphabet
            encoding a 128-bit number.

    Examples:
        >>> uuid_from_guid("0000000000000000000000")
        '00000000-0000-0000-0000-000000000000'
    """
    if not _GUID.fullmatch(guid):
        msg = f"not an IFC GlobalId: {guid!r}"
        raise ValueError(msg)
    value = 0
    for character in guid:
        value = value * 64 + GUID_ALPHABET.index(character)
    digits = f"{value:032x}"
    return f"{digits[:8]}-{digits[8:12]}-{digits[12:16]}-{digits[16:20]}-{digits[20:]}"


def product_guid(element: ET.Element) -> str | None:
    """Return the GlobalId a group names, if it names one.

    ``ifc:guid`` is the GlobalId as the model stores it and is preferred. Otherwise an
    ``id`` of ``product-<uuid>`` or ``product-<GlobalId>``, with anything after it such
    as ``-body``, is decoded (SPEC 6.4.1).

    Args:
        element: Any element.

    Returns:
        The GlobalId, or None.
    """
    stated = element.get(f"{_IFC}guid")
    if stated and re.fullmatch(r"[0-9A-Za-z_$]{22}", stated):
        return stated
    identifier = element.get("id", "")
    if not identifier.startswith("product-"):
        return None
    rest = identifier.removeprefix("product-")
    uuid = _UUID.match(rest)
    if uuid is not None:
        return guid_from_uuid(uuid.group(0))
    if _GUID.match(rest) and (len(rest) == _GUID_LENGTH or rest[_GUID_LENGTH] == "-"):
        return rest[:_GUID_LENGTH]
    return None


def ifc_class_of(element: ET.Element) -> str | None:
    """Return the IFC class among a group's ``class`` values.

    Args:
        element: Any element.

    Returns:
        The first value that is an IFC class name, or None.
    """
    for value in element.get("class", "").split():
        if _IFC_CLASS.fullmatch(value):
            return value
    return None


# ---------------------------------------------------------------------------
# Transforms and viewports
# ---------------------------------------------------------------------------
def compose(outer: Matrix, inner: Matrix) -> Matrix:
    """Return ``outer . inner``: apply ``inner`` first.

    Args:
        outer: The transform applied second.
        inner: The transform applied first.

    Returns:
        The composition.
    """
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(matrix: Matrix, x: float, y: float) -> Point:
    """Apply a transform to a point.

    Args:
        matrix: The transform.
        x: The point's x.
        y: Its y.

    Returns:
        The transformed point.
    """
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(text: str) -> Matrix:
    """Parse an SVG ``transform`` attribute.

    Args:
        text: The attribute, such as ``translate(25,12) scale(2)``.

    Returns:
        The transform it describes. A malformed one is logged and read as the identity,
        which is what leaves the rest of the drawing readable.
    """
    result = IDENTITY
    for name, arguments in _TRANSFORM.findall(text):
        values = [float(value) for value in _NUMBER.findall(arguments)]
        step = _transform_step(name, values)
        if step is None:
            logger.warning("ignoring a malformed transform: %s(%s)", name, arguments)
            return IDENTITY
        result = compose(result, step)
    return result


def _transform_step(name: str, values: Sequence[float]) -> Matrix | None:  # noqa: PLR0911 -- one per function
    """Return one transform function as a matrix.

    Args:
        name: ``matrix``, ``translate``, ``scale``, ``rotate``, ``skewX`` or ``skewY``.
        values: Its arguments.

    Returns:
        The matrix, or None when the argument count is wrong.
    """
    count = len(values)
    if name == "matrix" and count == 6:  # noqa: PLR2004 -- the six of an affine
        a, b, c, d, e, f = values
        return (a, b, c, d, e, f)
    if name == "translate" and count in {1, 2}:
        return (1.0, 0.0, 0.0, 1.0, values[0], values[1] if count == 2 else 0.0)  # noqa: PLR2004
    if name == "scale" and count in {1, 2}:
        return (values[0], 0.0, 0.0, values[-1], 0.0, 0.0)
    if name == "rotate" and count in {1, 3}:
        angle = math.radians(values[0])
        cos, sin = math.cos(angle), math.sin(angle)
        rotation = (cos, sin, -sin, cos, 0.0, 0.0)
        if count == 1:
            return rotation
        cx, cy = values[1], values[2]
        return compose(
            compose((1.0, 0.0, 0.0, 1.0, cx, cy), rotation), (1.0, 0.0, 0.0, 1.0, -cx, -cy)
        )
    if name == "skewX" and count == 1:
        return (1.0, 0.0, math.tan(math.radians(values[0])), 1.0, 0.0, 0.0)
    if name == "skewY" and count == 1:
        return (1.0, math.tan(math.radians(values[0])), 0.0, 1.0, 0.0, 0.0)
    return None


def _fit(view_box: Sequence[float], box: Sequence[float], aspect: str | None) -> Matrix:
    """Map a ``viewBox`` into a viewport, as ``preserveAspectRatio`` says.

    Args:
        view_box: ``(min-x, min-y, width, height)`` of the content; or ``(width,
            height)`` for content with no ``viewBox``, taken to start at the origin.
        box: The viewport ``(x, y, width, height)``.
        aspect: The ``preserveAspectRatio`` value; None is the default,
            ``xMidYMid meet``.

    Returns:
        The content's user units to the viewport's.
    """
    vx, vy, vw, vh = view_box if len(view_box) == 4 else (0.0, 0.0, *view_box)  # noqa: PLR2004
    x, y, width, height = box
    if vw <= 0 or vh <= 0:
        return (1.0, 0.0, 0.0, 1.0, x, y)
    sx, sy = width / vw, height / vh
    words = (aspect or "xMidYMid meet").split()
    align = words[0] if words else "xMidYMid"
    if align == "none":
        return (sx, 0.0, 0.0, sy, x - vx * sx, y - vy * sy)
    scale = max(sx, sy) if "slice" in words else min(sx, sy)
    spare_x, spare_y = width - vw * scale, height - vh * scale
    share_x = 0.0 if "xMin" in align else 1.0 if "xMax" in align else 0.5
    share_y = 0.0 if "YMin" in align else 1.0 if "YMax" in align else 0.5
    return (
        scale,
        0.0,
        0.0,
        scale,
        x + spare_x * share_x - vx * scale,
        y + spare_y * share_y - vy * scale,
    )


def _root_viewport(root: ET.Element) -> tuple[float, float, Matrix]:
    """Size the page and map the root's user units onto it in millimetres.

    A root with a ``viewBox`` and no size is taken to be in millimetres, which is how
    the serializer and Bonsai write sheets; one with a size and no ``viewBox`` has user
    units of CSS pixels, as SVG defines.

    Args:
        root: The document element.

    Returns:
        The page's width and height in millimetres, and the root's user units to page
        millimetres.

    Raises:
        CarrierError: If neither a size nor a ``viewBox`` says how large the page is.
    """
    view_box = _view_box(root)
    width = _length_mm(root.get("width"))
    height = _length_mm(root.get("height"))
    if width is None or height is None:
        if view_box is None:
            msg = "the SVG has neither a width and height nor a viewBox, so it has no page size"
            raise CarrierError(msg)
        width, height = view_box[2], view_box[3]
    if view_box is None:
        pixel = _MM_PER_UNIT["px"]
        return width, height, (pixel, 0.0, 0.0, pixel, 0.0, 0.0)
    placement = _fit(view_box, (0.0, 0.0, width, height), root.get("preserveAspectRatio"))
    return width, height, placement


def _nested_viewport(element: ET.Element) -> Matrix:
    """Map a nested ``<svg>``'s content into the viewport it establishes.

    Args:
        element: The nested ``<svg>``.

    Returns:
        Its content's user units to its parent's.
    """
    view_box = _view_box(element)
    x = _number(element.get("x")) or 0.0
    y = _number(element.get("y")) or 0.0
    width = _number(element.get("width"))
    height = _number(element.get("height"))
    if view_box is None or width is None or height is None:
        return (1.0, 0.0, 0.0, 1.0, x, y)
    return _fit(view_box, (x, y, width, height), element.get("preserveAspectRatio"))


def _box(image: ET.Element, child: ET.Element) -> tuple[float, float, float, float] | None:
    """Return the viewport an ``<image>`` gives the document it references.

    Args:
        image: The ``<image>``.
        child: The referenced document's root.

    Returns:
        ``(x, y, width, height)``, the size defaulting to the document's own, or None
        when neither says.
    """
    width = _number(image.get("width"))
    height = _number(image.get("height"))
    if width is None or height is None:
        intrinsic = _view_box(child)
        if intrinsic is None:
            logger.warning("an <image> gives no size and its SVG has no viewBox; skipped")
            return None
        width, height = intrinsic[2], intrinsic[3]
    return (_number(image.get("x")) or 0.0, _number(image.get("y")) or 0.0, width, height)


def _view_box(element: ET.Element) -> tuple[float, float, float, float] | None:
    """Read a ``viewBox``.

    Args:
        element: An ``<svg>``.

    Returns:
        ``(min-x, min-y, width, height)``, or None when absent or malformed.
    """
    values = [float(value) for value in _NUMBER.findall(element.get("viewBox", ""))]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:  # noqa: PLR2004
        return None
    return (values[0], values[1], values[2], values[3])


def _length_mm(text: str | None) -> float | None:
    """Convert an SVG length to millimetres.

    Args:
        text: A length such as ``420mm`` or ``1190.55pt``.

    Returns:
        Millimetres, or None when absent, a percentage, or not a length.
    """
    if text is None:
        return None
    match = _LENGTH.fullmatch(text)
    if match is None or match.group(2).lower() not in _MM_PER_UNIT:
        return None
    value = float(match.group(1)) * _MM_PER_UNIT[match.group(2).lower()]
    return value if value > 0 else None


def _number(text: str | None) -> float | None:
    """Read the number at the start of an attribute, ignoring any unit after it.

    Args:
        text: The attribute.

    Returns:
        The number, or None.
    """
    if text is None:
        return None
    match = _NUMBER.match(text.strip())
    return None if match is None else float(match.group(0))


def _matrix3(text: str) -> Matrix3 | None:
    """Read ``ifc:matrix3``'s top two rows.

    Args:
        text: The attribute, ``[[a,b,c],[d,e,f],[0,0,1]]``.

    Returns:
        The two rows that carry the affine, or None when it is not a 3x3 matrix.
    """
    values = [float(value) for value in _NUMBER.findall(text)]
    if len(values) != 9:  # noqa: PLR2004 -- three rows of three
        return None
    return ((values[0], values[1], values[2]), (values[3], values[4], values[5]))


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def _outline(name: str, element: ET.Element, matrix: Matrix, height_mm: float) -> Outline:
    """Return what one shape draws, in paper millimetres, y up.

    Args:
        name: The element's local name.
        element: The element.
        matrix: Its user units to page millimetres, y down.
        height_mm: The page height, for the flip of SPEC 3.3.

    Returns:
        The shape's points, rounded, with repeats dropped; empty for anything that is
        not a shape.
    """
    local = _shape_points(name, element)
    outline: list[Point] = []
    for x, y in local:
        page_x, page_y = apply(matrix, x, y)
        point = (round(page_x, COORD_DECIMALS), round(height_mm - page_y, COORD_DECIMALS))
        if not outline or outline[-1] != point:
            outline.append(point)
    return tuple(outline)


def _shape_points(name: str, element: ET.Element) -> list[Point]:  # noqa: PLR0911 -- one per shape
    """Return a shape's points in its own user units.

    Args:
        name: The element's local name.
        element: The element.

    Returns:
        The points; empty for an element that is not a shape or cannot be read.
    """

    def attribute(key: str) -> float:
        return _number(element.get(key)) or 0.0

    if name == "path":
        try:
            segments = list(SvgPath(element.get("d", "")).segments())
        except (ValueError, IndexError):
            logger.debug("an unreadable path was skipped")
            return []
        return [point for segment in segments for point in segment_points(segment)]
    if name == "rect":
        x, y, w, h = attribute("x"), attribute("y"), attribute("width"), attribute("height")
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    if name == "line":
        return [(attribute("x1"), attribute("y1")), (attribute("x2"), attribute("y2"))]
    if name in {"polyline", "polygon"}:
        values = [float(value) for value in _NUMBER.findall(element.get("points", ""))]
        points = list(zip(values[0::2], values[1::2], strict=False))
        return points + points[:1] if name == "polygon" else points
    if name in {"circle", "ellipse"}:
        cx, cy = attribute("cx"), attribute("cy")
        rx = attribute("r") if name == "circle" else attribute("rx")
        ry = attribute("r") if name == "circle" else attribute("ry")
        return [
            (
                cx + rx * math.cos(2 * math.pi * step / _ELLIPSE_SIDES),
                cy + ry * math.sin(2 * math.pi * step / _ELLIPSE_SIDES),
            )
            for step in range(_ELLIPSE_SIDES + 1)
        ]
    return []


# ---------------------------------------------------------------------------
# Reading safely
# ---------------------------------------------------------------------------
def _read_bounded(path: Path) -> bytes:
    """Read a file no larger than :data:`MAX_SVG_BYTES`.

    Args:
        path: The file.

    Returns:
        Its bytes.

    Raises:
        CarrierError: If it is larger.
    """
    size = path.stat().st_size
    if size > MAX_SVG_BYTES:
        msg = f"{path} is {size} bytes; an SVG larger than {MAX_SVG_BYTES} is not read"
        raise CarrierError(msg)
    return path.read_bytes()


def _parse_root(data: bytes) -> ET.Element:
    """Parse an SVG document, refusing what an attacker would send.

    Args:
        data: The document.

    Returns:
        Its root element.

    Raises:
        CarrierError: If it is too large, declares a DTD or entities, is not
            well-formed, or is not an SVG.
    """
    if len(data) > MAX_SVG_BYTES:
        msg = f"an SVG larger than {MAX_SVG_BYTES} bytes is not read"
        raise CarrierError(msg)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        msg = "the SVG declares a DTD; it is refused, since entity expansion is an attack"
        raise CarrierError(msg)
    try:
        root = ET.fromstring(data)  # noqa: S314 -- no DTD, so no entities to expand
    except ET.ParseError as error:
        msg = f"the SVG is not well-formed XML: {error}"
        raise CarrierError(msg) from error
    if _local(root.tag) != "svg":
        msg = f"the document is not an SVG: its root is {root.tag!r}"
        raise CarrierError(msg)
    return root


def _local(tag: object) -> str | None:
    """Return an SVG element's local name.

    Args:
        tag: The element's tag, namespaced as ElementTree spells it.

    Returns:
        The local name for an SVG element, or one with no namespace; None for anything
        else, including comments and processing instructions.
    """
    if not isinstance(tag, str):
        return None
    if tag.startswith(_SVG):
        return tag.removeprefix(_SVG)
    return None if tag.startswith("{") else tag


def _local_reference(href: str, base: Path | None) -> Path | None:
    """Resolve an ``<image>`` reference to a local SVG, if that is what it is.

    Args:
        href: The reference.
        base: The directory it is relative to.

    Returns:
        The file, or None for a URL, a ``data:`` URI, an absolute path, anything that is
        not an ``.svg``, or a file that does not exist.
    """
    if base is None or not href or ":" in href or href.startswith(("/", "\\")):
        return None
    target = (base / href).resolve()
    if target.suffix.lower() != ".svg" or not target.is_file():
        logger.debug("not following image reference %s", href)
        return None
    return target
