# SPDX-License-Identifier: Apache-2.0
"""Check the PlanLabel fixture corpus: canonical bytes, manifests, and drawing sense.

The fixtures under ``tests/fixtures`` are what a reader copies when learning the format
and what every other test measures itself against, so a fixture that is schema-legal
and physically impossible costs twice. This script turns that class of defect into
something CI catches.

It is not a second validator
----------------------------
Since Phase 3 it is not. Every rule about the **format** -- schema validity, identity
and references, geometry, provenance -- belongs to :mod:`planlabel.validate`, and this
script applies them by calling it: :func:`schema_findings` for rule 1, and
:func:`planlabel.validate.check_page_document` and
:func:`planlabel.validate.check_page_label` for the rest. It keeps no copy of any of
them. That is the point: four rounds of Phase 2 were spent on defects that
existed only because two pieces of code believed slightly different things, and the
fixture corpus checking the format with its own arithmetic was the largest remaining
instance of it.

What is left here is everything that is about **this corpus** rather than about
PlanLabel, and each item below says why it stays.

The manifests and the bytes
---------------------------
a. every document under ``valid/`` validates against its schema with zero errors, and
   the validator agrees;
b. every document under ``invalid/`` produces exactly **one** schema error, of the
   keyword, at the ``errorPath`` and with the ``validatorSchemaPath`` the manifest
   states. This is a claim about the manifest, not about the format: a negative fixture
   that fails for two reasons tests neither of them;
c. every document round-trips **byte for byte** -- through the models for a valid one,
   and as canonical JSON text for an invalid one, which no model can load. The
   comparison is on bytes and never on text, because :meth:`pathlib.Path.read_text`
   translates CRLF to LF and would hide a Windows line ending completely;
d. the manifest lists exactly the files on disk, in filename order, as many of each as
   the Phase 1 gate fixed, and every ``schemaPointer`` dereferences;
e. :func:`planlabel.model.conformance_level` returns the level the manifest, the
   filename and every index entry naming that sheet all claim.

The corpus is a drawing, not only a document
--------------------------------------------
These rules need a drawing to be plausible as well as well-formed. None is in the
specification, none could be -- a conforming label may describe an unbuildable
building -- and a real project's drawings would trip several of them for good reasons.

* **a level agrees with its own transform.** A level annotation's paper position, taken
  through ``paperToPlane`` and the plane, must yield the elevation it prints. This is a
  drafting convention (the tag sits at the height it names), not a format rule;
* **a section mark lies on the plane of the section it opens.** Likewise;
* **what a tag prints.** A tag showing ``Tag`` prints the element's tag exactly, and a
  tag showing a numeric property prints that number. Whether the element carries the
  property at all is the validator's PL-REF-012; what the sheet prints against it is
  not in the format at any level, and is here;
* **declared size versus drawn size.** Where an element's own name or its own IFC base
  quantity states a dimension, the drawing must agree to within 2 per cent;
* **physical plausibility.** A foundation above the model datum, a storey height outside
  2.2 to 6 m, a slab thinner than 100 mm or thicker than 500 mm;
* **cross-sheet consistency.** An element cut under the same name on two sheets of one
  model must be drawn at the same size. No single fixture can fail this;
* **model identity.** Two documents that declare the same ``model.sha256`` must agree on
  ``lengthUnit`` and on ``schema``, and ``file`` and ``sha256`` must determine each
  other. One file cannot be two models. Also cross-document;
* **the index against the labels.** Matched here by printed sheet id, because the
  corpus's indexes and labels are separate fixture files describing no one document;
  the validator matches them by page inside a carrier, which is a different rule about
  a different thing;
* **GlobalIds and sheet sizes are real.** A GlobalId's first character encodes two bits,
  so it can only be ``0`` to ``3``; the schema's pattern is deliberately looser, and a
  page that is not an A-series size is a fixture nobody meant to write.

Warnings count as failures here. On the command line a label with warnings and no
errors is conforming and exits 0, as section 4.4 requires. In this corpus a warning is
a defect: these are the twelve labels a reader copies, and one that trips a SHOULD
teaches the SHOULD wrongly.

Dimensions read off a name
--------------------------
:func:`name_dimensions` implements exactly the spellings this corpus uses, and nothing
it does not:

===================================  ==========================================
Spelling                             Read as
===================================  ==========================================
``Aussenwand 36,5 cm``               a thickness in centimetres, comma decimal
``Perimeterdaemmung 120 mm``         a thickness in millimetres
``Decke ueber EG, d = 25 cm``        a thickness, ``d`` for *Dicke*
``Fenster F-05 1,26/1,38``           width and height in metres, in that order
``Zuluft ZU-01 400x200``             duct width and height in millimetres
===================================  ==========================================

A bare ``1,20 m`` is deliberately **not** read as a thickness, and neither is
``h = 1,20 m``: a height is not an extent a plan can show. A cadastral parcel number --
``Flurstueck 112/4`` -- is not a window, which is why the width-over-height spelling
demands a decimal comma in both numbers and an ``IfcWindow`` or an ``IfcDoor`` to sit
on. Each rule below says in its own docstring which drawings it may be applied to;
where an element is drawn in a view that cannot show a dimension -- a slab's thickness
in a plan, a wall's height in a plan -- the dimension is not checked rather than
checked against the wrong extent.

Usage::

    uv run --frozen python tools/check_fixtures.py [FIXTURES_ROOT]

``FIXTURES_ROOT`` is the directory holding ``labels/`` and ``index/``; it defaults to
``tests/fixtures`` in this repository. Exits 0 when everything passes and 1 on the
first run that finds anything, printing the offending fixtures grouped by file.
"""

from __future__ import annotations

import codecs
import json
import re
import sys
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from planlabel.model import (
    Annotation,
    Element,
    LabelIndex,
    Model,
    PageLabel,
    Plane,
    Viewport,
    canonical_bytes,
    conformance_level,
    index_schema,
    load_label_index,
    load_page_label,
    page_schema,
)
from planlabel.validate import check_page_document, check_page_label
from planlabel.validate.geometric import (
    METRES_PER_MODEL_UNIT,
    MM_PER_MODEL_UNIT,
    millimetres,
)
from planlabel.validate.geometry import (
    apply_affine,
    bbox_centre,
    bbox_corners,
    cross,
    dot,
)
from planlabel.validate.geometry import (
    world_point as plane_to_world,
)
from planlabel.validate.schema import DocumentKind, check_schema

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from pydantic import BaseModel

    from planlabel.validate import Finding

#: Metres per value of ``model.lengthUnit``. The validator's table, not a second copy.
METRES_PER_UNIT = METRES_PER_MODEL_UNIT

#: Millimetres per value of ``model.lengthUnit``, for reading IFC quantities. Likewise.
MM_PER_UNIT = MM_PER_MODEL_UNIT

#: Millimetres per unit token written in an element's name.
MM_PER_NAME_UNIT = {"mm": 1.0, "cm": 10.0, "m": 1000.0}

#: A-series sheet sizes in millimetres, portrait. Either orientation is accepted.
A_SERIES = {
    (841.0, 1189.0),
    (594.0, 841.0),
    (420.0, 594.0),
    (297.0, 420.0),
    (210.0, 297.0),
    (148.0, 210.0),
}

#: The 64 characters an IFC GlobalId is written with, in IFC's own order.
IFC_GUID_RE = re.compile(r"^[0-3][0-9A-Za-z_$]{21}$")

#: How a fixture filename spells the conformance level it claims.
LEVEL_IN_NAME_RE = re.compile(r"-(l[123])\b", re.IGNORECASE)

#: Absolute tolerance on an elevation, in metres. Coordinates carry three decimals.
ELEVATION_ATOL_M = 1e-6

#: Tolerance on a plane axis being of unit length and orthogonal to its partner.
AXIS_ATOL = 1e-6

#: Decimal places a canonical paper coordinate carries, and therefore the precision at
#: which two of them are the same coordinate.
COORD_PLACES = 3

#: Relative tolerance between a dimension a document states and the size it is drawn
#: at. Wider than :data:`LENGTH_RTOL`, because a stated dimension is nominal and a
#: drawn one is what the line work happens to cover.
SIZE_RTOL = 0.02

#: Absolute floor of that tolerance, in model millimetres.
SIZE_ATOL_MM = 0.5

#: Plausible storey height in metres, measured between consecutive slabs in a section.
MIN_STOREY_HEIGHT_M = 2.2
MAX_STOREY_HEIGHT_M = 6.0

#: Plausible slab thickness in millimetres, measured where a section cuts the slab.
MIN_SLAB_THICKNESS_MM = 100.0
MAX_SLAB_THICKNESS_MM = 500.0

#: How many fixtures each of ``valid/`` and ``invalid/`` holds, per corpus. The Phase 1
#: gate fixes the count, so a fixture added without one being retired is itself a
#: defect.
FIXTURES_PER_DIRECTORY = {"labels": 12, "index": 3}


class Problem(NamedTuple):
    """One failed expectation.

    Attributes:
        where: The fixture, or the manifest, the problem was found in. Cross-document
            findings belong to no single file and carry ``cross-document`` instead.
        what: A one-line description, stating what was expected and what was found.
    """

    where: str
    what: str


class Drawn(NamedTuple):
    """One element as one fixture draws it, with everything a size rule needs.

    Attributes:
        fixture: The fixture the element was read from, as the manifest names it.
        sheet_id: The printed number of the sheet the element appears on.
        element: The element itself.
        viewport: The viewport it is drawn in.
        length_unit: ``model.lengthUnit``, or None when the label declares no model.
        sha256: ``model.sha256``, or None when the label declares no model.
    """

    fixture: str
    sheet_id: str
    element: Element
    viewport: Viewport
    length_unit: str | None
    sha256: str | None


class ModelRef(NamedTuple):
    """One document's account of the source model it was written from.

    Attributes:
        fixture: The fixture that declares it.
        sha256: The digest of the model file's contents.
        file: The model file's name.
        length_unit: The unit the model's coordinates are in.
        ifc_schema: The IFC schema the model is written in.
    """

    fixture: str
    sha256: str | None
    file: str | None
    length_unit: str | None
    ifc_schema: str | None


class Stated(NamedTuple):
    """One dimension a document states about an element, and where to look for it.

    Attributes:
        source: Where the dimension was read from, for the message.
        millimetres: The dimension in model millimetres.
        extent: Which extent of the drawing it should equal -- ``short``, ``long`` or
            ``height``.
    """

    source: str
    millimetres: float
    extent: str


# ---------------------------------------------------------------------------
# Corpus rules: is this a real drawing?
# ---------------------------------------------------------------------------
def check_identifiers_and_page_size(label: PageLabel) -> list[str]:
    """Check that GlobalIds and page sizes are real ones.

    Args:
        label: The page label to check.

    Returns:
        One message per malformed GlobalId, plus one if the page is not A-series.
    """
    found: list[str] = []
    guids: list[tuple[str, str | None]] = [
        (f"viewport {vp.local_id}", vp.storey.ifc_guid if vp.storey else None)
        for vp in label.viewports or []
    ]
    guids += [(f"element {item.local_id}", item.ifc_guid) for item in label.elements or []]
    guids += [(f"annotation {item.local_id}", item.ifc_guid) for item in label.annotations or []]
    for name, guid in guids:
        if guid is not None and not IFC_GUID_RE.match(guid):
            found.append(f"{name}: {guid!r} is not a 22-character IFC GlobalId")
    size = (label.page.width_mm, label.page.height_mm)
    if size not in A_SERIES and (size[1], size[0]) not in A_SERIES:
        found.append(f"page: {size[0]}x{size[1]} mm is not an A-series sheet size")
    return found


# ---------------------------------------------------------------------------
# Corpus rules: drafting conventions the specification does not require
# ---------------------------------------------------------------------------
def check_levels(label: PageLabel) -> list[str]:
    """Check that a level's elevation matches its viewport's transform.

    A level annotation sits at a height in the model. Its viewport says what a paper
    position means: ``paperToPlane`` gives the point on the plane, and the plane's
    origin and axes give the point in the model. The world z of the annotation's own
    paper position must therefore be the elevation it prints.

    Args:
        label: The page label to check.

    Returns:
        One message per level that contradicts its viewport.
    """
    viewports = {viewport.local_id: viewport for viewport in label.viewports or []}
    unit = label.source_model.length_unit if label.source_model else None
    metres = METRES_PER_UNIT.get(unit or "m", 1.0)
    found: list[str] = []
    for annotation in label.annotations or []:
        if annotation.annotation_type != "level" or annotation.elevation is None:
            continue
        viewport = viewports.get(annotation.viewport or "")
        if viewport is None or viewport.plane is None or viewport.paper_to_plane is None:
            continue
        x, y = bbox_centre(annotation.paper_bbox)
        plane_x, plane_y = apply_affine(viewport.paper_to_plane, x, y)
        plane = viewport.plane
        world_z = plane.origin[2] + plane_x * plane.x_axis[2] + plane_y * plane.y_axis[2]
        yielded = world_z * metres
        if abs(yielded - annotation.elevation) > ELEVATION_ATOL_M:
            found.append(
                f"annotation {annotation.local_id}: declares elevation "
                f"{annotation.elevation:g} m, but viewport {viewport.local_id} yields "
                f"{yielded:.3f} m at paper ({x:g}, {y:g})"
            )
    return found


def check_section_marks(label: PageLabel) -> list[str]:
    """Check that a section mark lies on the plane of the section it opens.

    A section mark drawn on a plan is the cutting line, so the model points it runs
    through must lie on the target viewport's plane. A mark drawn half a metre off the
    plane it names is the same defect as a level that contradicts its own transform.

    Args:
        label: The page label to check.

    Returns:
        One message per section mark that misses its target plane.
    """
    viewports = {viewport.local_id: viewport for viewport in label.viewports or []}
    found: list[str] = []
    for annotation in label.annotations or []:
        placed = section_mark_placement(annotation, viewports, label.sheet.sheet_id)
        if placed is None:
            continue
        drawn_in, matrix, opened = placed
        normal = cross(opened.x_axis, opened.y_axis)
        offsets = [
            dot(
                [
                    world_point(drawn_in, matrix, point)[axis] - opened.origin[axis]
                    for axis in range(3)
                ],
                normal,
            )
            for point in annotation.geometry or []
        ]
        worst = max(offsets, key=abs)
        if abs(worst) > ELEVATION_ATOL_M:
            found.append(
                f"annotation {annotation.local_id}: the mark lies {worst:.3f} off the plane "
                f"of the viewport it opens"
            )
    return found


def world_point(
    plane: Plane,
    matrix: Sequence[float],
    point: Sequence[float],
) -> tuple[float, float, float]:
    """Map a paper point into model space through a viewport's plane and transform.

    Composition and nothing else: the two steps are
    :func:`planlabel.validate.geometry.apply_affine` and
    :func:`planlabel.validate.geometry.world_point`, both of which the validator uses
    for the same purpose.

    Args:
        plane: The viewport's model-space plane.
        matrix: That viewport's ``paperToPlane``.
        point: The paper point, in millimetres.

    Returns:
        The point in the model's coordinates, in the model's length unit.
    """
    return plane_to_world(
        plane.origin, plane.x_axis, plane.y_axis, apply_affine(matrix, point[0], point[1])
    )


def section_mark_placement(
    annotation: Annotation,
    viewports: dict[str, Viewport],
    sheet_id: str,
) -> tuple[Plane, Sequence[float], Plane] | None:
    """Return what is needed to check one section mark, or None if it cannot be checked.

    Args:
        annotation: The annotation to inspect.
        viewports: Every viewport on the page, by local id.
        sheet_id: The id of the sheet the label describes.

    Returns:
        The plane and transform of the viewport the mark is drawn in, and the plane of
        the viewport it opens. None when the annotation is not a section mark carrying
        geometry, is not placed in a viewport that maps paper to the model, or points at
        a viewport on another sheet, whose plane this label does not hold.
    """
    if annotation.annotation_type != "sectionMark" or annotation.geometry is None:
        return None
    target = annotation.target
    if target is None or target.viewport_id is None or target.sheet_id not in (None, sheet_id):
        return None
    drawn_in = viewports.get(annotation.viewport or "")
    opened = viewports.get(target.viewport_id)
    if drawn_in is None or drawn_in.plane is None or drawn_in.paper_to_plane is None:
        return None
    if opened is None or opened.plane is None:
        return None
    return (drawn_in.plane, drawn_in.paper_to_plane, opened.plane)


# ---------------------------------------------------------------------------
# Corpus rules: what a tag prints
# ---------------------------------------------------------------------------
#: The first number in a piece of annotation text, with the unit token it carries.
PRINTED_NUMBER_RE = re.compile(r"(?<![\d,.])(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?(?![A-Za-z0-9])")


def check_shown_properties(label: PageLabel) -> list[str]:
    """Check that a tag prints what it says it shows.

    Whether ``shows.element`` resolves at all is PL-REF-003, which the validator owns.
    What is left here is about the drawing rather than about the format: a property set
    the element does not carry, and a printed number that contradicts the property the
    tag says it displays. Nothing in the specification requires a ``shows.property`` to
    name a property the element holds -- the schema calls it free text with two
    examples -- so this is a statement about this corpus and not about PlanLabel.

    Args:
        label: The page label to check.

    Returns:
        One message per ``shows.property`` the element does not carry, and per printed
        text that contradicts the property it says it displays.
    """
    elements = {element.local_id: element for element in label.elements or []}
    unit = label.source_model.length_unit if label.source_model else None
    found: list[str] = []
    for annotation in label.annotations or []:
        shows = annotation.shows
        if shows is None or shows.element is None:
            continue
        element = elements.get(shows.element)
        if element is None:
            continue
        found.extend(check_shown_property(annotation, element, unit))
    return found


def check_shown_property(
    annotation: Annotation,
    element: Element,
    unit: str | None,
) -> list[str]:
    """Check one ``shows.property`` against the element it reads from.

    Two spellings are resolvable and are checked. ``Tag`` is the element's own ``tag``,
    which the annotation must print verbatim. ``Pset_X.Y`` is a property of a property
    set: where the element carries it and the annotation's text contains a number, the
    two must agree. Whether the element carries it at all is PL-REF-012, which the
    validator owns; this is only about what the sheet prints. Any other spelling -- a
    bare IFC attribute name, say -- is left alone, because nothing in the label says
    where to look it up.

    Args:
        annotation: The annotation doing the showing.
        element: The element it names.
        unit: ``model.lengthUnit``, used to decide whether a printed unit token can be
            compared with the stored value at all.

    Returns:
        One message per disagreement.
    """
    name = annotation.shows.property_name if annotation.shows else None
    if name is None:
        return []
    if name == "Tag":
        return check_shown_tag(annotation, element)
    if "." not in name:
        return []
    set_name, _, property_name = name.partition(".")
    quantities = element.pset(set_name)
    if quantities is None or property_name not in quantities:
        # PL-REF-012 owns this: whether the element carries what the tag says it shows.
        return []
    return check_printed_number(annotation, quantities[property_name], unit)


def check_shown_tag(annotation: Annotation, element: Element) -> list[str]:
    """Check that a tag showing ``Tag`` prints the element's tag and nothing else.

    A tag is the number the sheet prints against a thing, and it is how a reader on
    site joins the drawing to the schedule. One that says it shows an element's tag and
    then prints something else is worse than one that shows nothing.

    Args:
        annotation: The annotation doing the showing.
        element: The element it names.

    Returns:
        A single message when the element carries no tag at all, or when the text and
        the tag differ, and nothing when the annotation prints no text.
    """
    if element.tag is None:
        message = (
            f"annotation {annotation.local_id}: shows the Tag of element "
            f"{element.local_id}, which carries none"
        )
        return [message]
    if annotation.text is not None and annotation.text != element.tag:
        message = (
            f"annotation {annotation.local_id}: shows the Tag of element "
            f"{element.local_id} and prints {annotation.text!r}, but that tag is "
            f"{element.tag!r}"
        )
        return [message]
    return []


def check_printed_number(annotation: Annotation, value: object, unit: str | None) -> list[str]:
    """Check that the number a tag prints is the number it says it shows.

    Args:
        annotation: The annotation, whose ``text`` is what the sheet prints.
        value: The value held by the property the annotation says it shows.
        unit: ``model.lengthUnit``. A text that spells out a unit other than the
            model's is not compared: the conversion would be a guess.

    Returns:
        A single message when the printed number contradicts the stored one, and
        nothing when either is absent, is not a number, or cannot be compared.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return []
    printed = printed_number(annotation.text)
    if printed is None:
        return []
    number, decimals, token = printed
    if token is not None and token != unit:
        return []
    tolerance = max(0.5 * 10.0**-decimals, SIZE_RTOL * abs(value))
    if abs(number - value) <= tolerance:
        return []
    message = (
        f"annotation {annotation.local_id}: prints {annotation.text!r}, but the property "
        f"it shows holds {value:g}"
    )
    return [message]


def printed_number(text: str | None) -> tuple[float, int, str | None] | None:
    """Read the first number out of a piece of annotation text.

    Args:
        text: The text as the sheet prints it, with a German decimal comma or an
            English point.

    Returns:
        The number, how many decimal places it was printed to, and the unit token that
        followed it if any. None when the text holds no number.
    """
    if text is None:
        return None
    match = PRINTED_NUMBER_RE.search(text)
    if match is None:
        return None
    spelled = match.group(1).replace(",", ".")
    _, _, fraction = spelled.partition(".")
    return (float(spelled), len(fraction), match.group(2))


# ---------------------------------------------------------------------------
# Declared size versus drawn size
# ---------------------------------------------------------------------------
#: ``Aussenwand 36,5 cm``, ``Perimeterdaemmung 120 mm``: a bare thickness in a name.
#: Metres are deliberately excluded -- a number of metres in a name is a length or a
#: height far more often than a thickness, and guessing which would cry wolf.
NAME_THICKNESS_RE = re.compile(r"(?<![\d,.])(\d+(?:,\d+)?)\s*(mm|cm)(?![A-Za-z])")

#: ``Decke ueber EG, d = 25 cm``: ``d`` for *Dicke*, the German thickness shorthand.
NAME_DICKE_RE = re.compile(r"(?<![A-Za-z])d\s*=\s*(\d+(?:,\d+)?)\s*(mm|cm|m)(?![A-Za-z])")

#: ``Zuluft ZU-01 400x200``: a duct's clear width by its clear height, in millimetres.
NAME_DUCT_RE = re.compile(r"(?<!\d)(\d{2,4})\s*[xX]\s*(\d{2,4})(?!\d)")

#: ``Fenster F-05 1,26/1,38``: width over height in metres. Both numbers must carry a
#: decimal comma, which is what keeps a cadastral parcel number -- ``Flurstueck 112/4``
#: -- from being read as a window 112 m wide.
NAME_SASH_RE = re.compile(r"(?<![\d,])(\d+,\d+)\s*/\s*(\d+,\d+)(?![\d,])")

#: The IFC base-quantity sets, whose members are in the model's own length unit.
QUANTITY_SET_RE = re.compile(r"^Qto_[A-Za-z0-9]*BaseQuantities$")

#: Which extent of a drawing each base quantity should equal.
QUANTITY_EXTENTS = (
    ("Width", "short"),
    ("Length", "long"),
    ("Depth", "height"),
    ("Height", "height"),
)

#: IFC classes whose name may carry a duct size, and whose plan extent is that width.
DUCT_CLASS_PREFIX = "IfcDuct"

#: IFC classes whose name may carry a German width/height pair.
SASH_CLASSES = frozenset({"IfcDoor", "IfcWindow"})


def german_number(spelled: str) -> float:
    """Read a number written with a German decimal comma.

    Args:
        spelled: The number as a drawing spells it, such as ``36,5``.

    Returns:
        Its value.
    """
    return float(spelled.replace(",", "."))


def drawn_extents_mm(item: Drawn) -> tuple[float, float] | None:
    """Return the two extents of an element's bbox, in model millimetres.

    A drawing at 1:S maps one paper millimetre to S model millimetres, whatever
    ``model.lengthUnit`` happens to be, so this needs the scale and nothing else.

    Args:
        item: The element as one fixture draws it.

    Returns:
        The short extent and the long one, or None when the viewport states no scale.
    """
    if item.viewport.scale is None:
        return None
    box = item.element.paper_bbox
    across = abs(box[2] - box[0]) * item.viewport.scale
    up = abs(box[3] - box[1]) * item.viewport.scale
    return (min(across, up), max(across, up))


def plane_resolves_height(plane: Plane) -> bool:
    """Report whether a view's plane carries the model's vertical direction.

    Args:
        plane: The viewport's plane.

    Returns:
        True for a section, an elevation or a vertical detail, where paper up is model
        up and a bbox therefore says how tall a thing is. False for a plan, where every
        point of the view sits at the cut height and an element's own height is not on
        the paper at all.
    """
    return abs(plane.x_axis[2]) > AXIS_ATOL or abs(plane.y_axis[2]) > AXIS_ATOL


def world_z_range(viewport: Viewport, box: Sequence[float]) -> tuple[float, float] | None:
    """Return the model heights a paper bbox spans, in the model's length unit.

    Args:
        viewport: The viewport the bbox is drawn in.
        box: The bbox, in paper millimetres.

    Returns:
        The lowest and highest model z of the bbox's corners, or None when the viewport
        does not map paper to the model or is a view in which height does not appear.
    """
    plane, matrix = viewport.plane, viewport.paper_to_plane
    if plane is None or matrix is None or not plane_resolves_height(plane):
        return None
    heights = [world_point(plane, matrix, corner)[2] for corner in bbox_corners(box)]
    return (min(heights), max(heights))


def drawn_height_mm(item: Drawn) -> float | None:
    """Return how tall an element is drawn, in model millimetres.

    Args:
        item: The element as one fixture draws it.

    Returns:
        The model height its bbox spans, or None when the view does not resolve height
        or the label declares no length unit to read the model's coordinates in.
    """
    if item.length_unit is None:
        return None
    span = world_z_range(item.viewport, item.element.paper_bbox)
    if span is None:
        return None
    return (span[1] - span[0]) * MM_PER_UNIT[item.length_unit]


def name_dimensions(item: Drawn) -> list[Stated]:
    """Read the dimensions an element's own name states.

    The spellings supported are the ones this corpus uses, and each is read only where
    the drawing could show it:

    * a bare thickness in millimetres or centimetres, and ``d = 25 cm``, are read as a
      material thickness and checked against the **short** extent -- but only on an
      element drawn ``cut``, since a thickness is what a cut through a thing reveals and
      a plan projection of a slab shows its area instead;
    * ``400x200`` on an ``IfcDuct*`` in a plan is the duct's clear width by its clear
      height; only the width is on the paper, as the **short** extent of the run;
    * ``1,26/1,38`` on a window or a door in a plan is width over height in metres; only
      the width is on the paper, as the **long** extent of the opening.

    Args:
        item: The element as one fixture draws it.

    Returns:
        Every dimension the name states that this drawing can be held to.
    """
    name = item.element.name
    if not name:
        return []
    element, kind = item.element, item.viewport.kind
    stated: list[Stated] = []
    thickness = NAME_DICKE_RE.search(name) or NAME_THICKNESS_RE.search(name)
    if thickness is not None and element.representation == "cut":
        millimetres = german_number(thickness.group(1)) * MM_PER_NAME_UNIT[thickness.group(2)]
        stated.append(Stated(f"its name states {thickness.group(0)!r}", millimetres, "short"))
    duct = NAME_DUCT_RE.search(name) if element.ifc_class.startswith(DUCT_CLASS_PREFIX) else None
    if duct is not None and kind == "plan":
        stated.append(
            Stated(f"its name states a duct {duct.group(0)!r}", float(duct.group(1)), "short")
        )
    sash = NAME_SASH_RE.search(name) if element.ifc_class in SASH_CLASSES else None
    if sash is not None and kind == "plan":
        stated.append(
            Stated(
                f"its name states a width of {sash.group(1)} m",
                german_number(sash.group(1)) * MM_PER_NAME_UNIT["m"],
                "long",
            )
        )
    return stated


def quantity_applies(item: Drawn, extent: str) -> bool:
    """Report whether a base quantity can be measured off this drawing at all.

    Args:
        item: The element as one fixture draws it.
        extent: The extent the quantity should equal.

    Returns:
        True when the drawing shows that extent. A quantity is only ever checked
        against an element drawn ``cut``, because a projected or symbolic element is
        drawn at the size the symbol wants; a length is only read off a plan; and a
        height or a depth is only read off a view whose plane carries model up.
    """
    if item.element.representation != "cut":
        return False
    if extent == "long":
        return item.viewport.kind == "plan"
    if extent == "height":
        return item.viewport.plane is not None and plane_resolves_height(item.viewport.plane)
    return True


def quantity_dimensions(item: Drawn) -> list[Stated]:
    """Read the dimensions an element's IFC base quantities state.

    ``Width`` is the short extent of an element, ``Length`` the long one, and ``Depth``
    and ``Height`` are both the vertical one. ``GrossArea`` is handled separately by
    :func:`check_gross_area`, since an area is a product and not an extent.

    Args:
        item: The element as one fixture draws it.

    Returns:
        Every base quantity this drawing can be held to, in model millimetres.
    """
    properties, unit = item.element.properties, item.length_unit
    if properties is None or unit is None:
        return []
    stated: list[Stated] = []
    for set_name in sorted(properties):
        quantities = item.element.pset(set_name)
        if quantities is None or not QUANTITY_SET_RE.match(set_name):
            continue
        for key, extent in QUANTITY_EXTENTS:
            value = quantities.get(key)
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            if not quantity_applies(item, extent):
                continue
            stated.append(
                Stated(
                    f"{set_name}.{key} states {value:g} {unit}",
                    value * MM_PER_UNIT[unit],
                    extent,
                )
            )
    return stated


def outline_is_bbox_rectangle(element: Element) -> bool:
    """Report whether an element's outline is exactly the rectangle of its bbox.

    Args:
        element: The element to inspect.

    Returns:
        True when the element carries one outline and that outline's corners are the
        four corners of its ``paperBBox``. Only then is the product of the bbox extents
        the element's real area; for an L-shaped slab the bbox is larger than the thing
        inside it, and comparing the two would report a defect that is not there.
    """
    outlines = element.paper_outlines
    if outlines is None or len(outlines) != 1:
        return False
    drawn = {
        (round(point[0], COORD_PLACES), round(point[1], COORD_PLACES)) for point in outlines[0]
    }
    corners = {
        (round(x, COORD_PLACES), round(y, COORD_PLACES))
        for x, y in bbox_corners(element.paper_bbox)
    }
    return drawn == corners


def check_gross_area(item: Drawn, extents: tuple[float, float]) -> list[str]:
    """Check a ``GrossArea`` base quantity against the area the element is drawn at.

    Args:
        item: The element as one fixture draws it.
        extents: Its short and long extent in model millimetres.

    Returns:
        A single message when the two disagree, and nothing when the quantity is absent
        or when the element's shape is not known to be its bounding rectangle.
    """
    properties, unit = item.element.properties, item.length_unit
    if properties is None or unit is None or item.viewport.kind != "plan":
        return []
    if not outline_is_bbox_rectangle(item.element):
        return []
    for set_name in sorted(properties):
        quantities = item.element.pset(set_name)
        if quantities is None or not QUANTITY_SET_RE.match(set_name):
            continue
        value = quantities.get("GrossArea")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        stated = value * METRES_PER_UNIT[unit] ** 2
        drawn = (extents[0] / 1000.0) * (extents[1] / 1000.0)
        if stated > 0 and abs(drawn - stated) > SIZE_RTOL * stated:
            message = (
                f"element {item.element.local_id}: {set_name}.GrossArea states "
                f"{stated:g} m2, but it is drawn {millimetres(extents[1])} x "
                f"{millimetres(extents[0])} mm = {drawn:g} m2 -- x{drawn / stated:.3f}"
            )
            return [message]
    return []


def check_stated_sizes(item: Drawn) -> list[str]:
    """Check every dimension an element states about itself against how it is drawn.

    Args:
        item: The element as one fixture draws it.

    Returns:
        One message per dimension the drawing contradicts by more than 2 per cent. A
        dimension stated twice -- once in the name and once as a base quantity -- is
        reported once.
    """
    extents = drawn_extents_mm(item)
    if extents is None:
        return []
    measured = {"short": extents[0], "long": extents[1], "height": drawn_height_mm(item)}
    found: list[str] = []
    seen: set[tuple[str, float]] = set()
    for stated in name_dimensions(item) + quantity_dimensions(item):
        key = (stated.extent, round(stated.millimetres, COORD_PLACES))
        drawn = measured[stated.extent]
        if key in seen or drawn is None or stated.millimetres <= 0:
            continue
        seen.add(key)
        if abs(drawn - stated.millimetres) <= max(SIZE_ATOL_MM, SIZE_RTOL * stated.millimetres):
            continue
        found.append(
            f"element {item.element.local_id}: {stated.source} = "
            f"{millimetres(stated.millimetres)} mm, but its {stated.extent} extent is drawn "
            f"{millimetres(drawn)} mm at 1:{item.viewport.scale:g} -- "
            f"x{drawn / stated.millimetres:.3f}"
        )
    return found + check_gross_area(item, extents)


# ---------------------------------------------------------------------------
# Model identity, physical plausibility and cross-sheet consistency
# ---------------------------------------------------------------------------
#: Elements whose name says they are part of the foundation.
FOUNDATION_NAME_RE = re.compile(r"fundament|sohle|foundation", re.IGNORECASE)

#: The IFC class of a foundation, for an element whose name does not say so.
FOOTING_CLASS = "IfcFooting"

#: The IFC class whose cut thickness a section states.
SLAB_CLASS = "IfcSlab"


def check_model_identity(declarations: list[ModelRef]) -> list[Problem]:
    """Check that a source model is one model wherever it is named.

    A ``sha256`` is the identity of a file's contents. Two documents that quote the
    same digest and then disagree about the model's length unit or its IFC schema
    cannot both be right, and a reader that believes the wrong one is out by a factor
    of a thousand or is looking for entities that are not there. The same holds of the
    pairing between ``file`` and ``sha256``: one name cannot be two contents, and one
    content in this corpus is not published under two names.

    Args:
        declarations: One entry per document that declares a model.

    Returns:
        One problem per digest whose documents disagree, and one per name-to-digest
        pairing that is not one to one.
    """
    found: list[Problem] = []
    found += disagreements(declarations, key="sha256", field="length_unit", label="lengthUnit")
    found += disagreements(declarations, key="sha256", field="ifc_schema", label="schema")
    found += disagreements(declarations, key="file", field="sha256", label="sha256")
    found += disagreements(declarations, key="sha256", field="file", label="file")
    return found


def disagreements(
    declarations: list[ModelRef],
    *,
    key: str,
    field: str,
    label: str,
) -> list[Problem]:
    """Report every value of ``key`` for which ``field`` is not single-valued.

    Args:
        declarations: The model declarations, as :func:`check_model_identity` takes
            them.
        key: Name of the member that identifies the model.
        field: Name of the member that must not vary within one key.
        label: How to spell the varying member in the message.

    Returns:
        One problem per key whose documents disagree, naming every value and the
        fixtures that state it.
    """
    grouped: dict[str, dict[str, list[str]]] = {}
    for declaration in declarations:
        identity, value = getattr(declaration, key), getattr(declaration, field)
        if identity is None or value is None:
            continue
        grouped.setdefault(identity, {}).setdefault(value, []).append(declaration.fixture)
    found: list[Problem] = []
    for identity in sorted(grouped):
        values = grouped[identity]
        if len(values) == 1:
            continue
        spelled = "; ".join(
            f"{value!r} in {', '.join(sorted(values[value]))}" for value in sorted(values)
        )
        found.append(
            Problem(
                "cross-document",
                f"model {key} {identity} is declared with more than one {label}: {spelled}",
            )
        )
    return found


def check_foundations(fixture: str, label: PageLabel) -> list[Problem]:
    """Check that nothing called a foundation is drawn above the model datum.

    Args:
        fixture: The fixture the label was read from.
        label: The page label to check.

    Returns:
        One problem per footing whose lowest drawn point is above z = 0.
    """
    viewports = {viewport.local_id: viewport for viewport in label.viewports or []}
    unit = label.source_model.length_unit if label.source_model else None
    found: list[Problem] = []
    for element in label.elements or []:
        named = FOUNDATION_NAME_RE.search(element.name or "") is not None
        if not named and element.ifc_class != FOOTING_CLASS:
            continue
        viewport = viewports.get(element.viewport or "")
        if viewport is None or unit is None:
            continue
        span = world_z_range(viewport, element.paper_bbox)
        if span is None or span[0] <= ELEVATION_ATOL_M:
            continue
        metres = METRES_PER_UNIT[unit]
        found.append(
            Problem(
                fixture,
                f"element {element.local_id}: {element.name or element.ifc_class} is drawn "
                f"between z = {span[0] * metres:.3f} m and z = {span[1] * metres:.3f} m, "
                f"above the model datum",
            )
        )
    return found


def check_slab_thickness(fixture: str, label: PageLabel) -> list[Problem]:
    """Check that every slab a section cuts has a buildable thickness.

    Args:
        fixture: The fixture the label was read from.
        label: The page label to check.

    Returns:
        One problem per cut slab thinner than 100 mm or thicker than 500 mm.
    """
    found: list[Problem] = []
    for item in drawn_elements(fixture, label):
        element = item.element
        if element.ifc_class != SLAB_CLASS or element.representation != "cut":
            continue
        thickness = drawn_height_mm(item)
        if thickness is None:
            continue
        if MIN_SLAB_THICKNESS_MM <= thickness <= MAX_SLAB_THICKNESS_MM:
            continue
        found.append(
            Problem(
                fixture,
                f"element {element.local_id}: {element.name or element.ifc_class} is cut "
                f"{millimetres(thickness)} mm thick, outside the buildable "
                f"{MIN_SLAB_THICKNESS_MM:g}-{MAX_SLAB_THICKNESS_MM:g} mm",
            )
        )
    return found


def check_storey_heights(fixture: str, label: PageLabel) -> list[Problem]:
    """Check the storey heights a section states between the slabs it cuts.

    Args:
        fixture: The fixture the label was read from.
        label: The page label to check.

    Returns:
        One problem per pair of consecutive slabs whose tops are less than 2.2 m or
        more than 6 m apart, which is a storey nobody can stand up in or a slab drawn
        at the wrong height.
    """
    unit = label.source_model.length_unit if label.source_model else None
    if unit is None:
        return []
    metres = METRES_PER_UNIT[unit]
    stacks: dict[str, list[tuple[float, str]]] = {}
    for item in drawn_elements(fixture, label):
        if item.element.ifc_class != SLAB_CLASS or item.element.representation != "cut":
            continue
        span = world_z_range(item.viewport, item.element.paper_bbox)
        if span is None:
            continue
        stacks.setdefault(item.viewport.local_id, []).append((span[1], item.element.local_id))
    found: list[Problem] = []
    for viewport_id in sorted(stacks):
        tops = sorted(stacks[viewport_id])
        for lower, upper in pairwise(tops):
            height = (upper[0] - lower[0]) * metres
            if MIN_STOREY_HEIGHT_M <= height <= MAX_STOREY_HEIGHT_M:
                continue
            found.append(
                Problem(
                    fixture,
                    f"viewport {viewport_id}: the storey between {lower[1]} and {upper[1]} "
                    f"is {height:.3f} m, outside the plausible "
                    f"{MIN_STOREY_HEIGHT_M:g}-{MAX_STOREY_HEIGHT_M:g} m",
                )
            )
    return found


def check_cross_sheet_sizes(items: list[Drawn]) -> list[Problem]:
    """Check that one element cut on two sheets is drawn at one size.

    Two entries are treated as the same thing when they carry the same ``name``, come
    from the same ``model.sha256`` and are drawn in viewports of the same ``kind``.
    Only elements drawn ``cut`` are compared: a cut shows the thing itself at the
    cutting plane, whereas a projection shows as much of it as the view happens to
    crop, and two sections may legitimately crop one stair differently.

    Args:
        items: Every element of every valid label, as its fixture draws it.

    Returns:
        One problem per pair that disagrees by more than 2 per cent in either extent.
    """
    grouped: dict[tuple[str, str, str], list[Drawn]] = {}
    for item in items:
        if item.sha256 is None or not item.element.name:
            continue
        if item.element.representation != "cut" or drawn_extents_mm(item) is None:
            continue
        grouped.setdefault((item.element.name, item.sha256, item.viewport.kind), []).append(item)
    found: list[Problem] = []
    for key in sorted(grouped):
        members = grouped[key]
        first = members[0]
        base = drawn_extents_mm(first)
        for other in members[1:]:
            size = drawn_extents_mm(other)
            if base is None or size is None or extents_agree(base, size):
                continue
            found.append(
                Problem(
                    "cross-document",
                    f"element {key[0]!r} of model {key[1][:12]} is cut "
                    f"{millimetres(base[1])} x {millimetres(base[0])} mm on sheet "
                    f"{first.sheet_id} ({first.fixture}, {first.element.local_id}) and "
                    f"{millimetres(size[1])} x {millimetres(size[0])} mm on sheet "
                    f"{other.sheet_id} ({other.fixture}, {other.element.local_id})",
                )
            )
    return found


def extents_agree(left: tuple[float, float], right: tuple[float, float]) -> bool:
    """Report whether two drawn sizes are the same size.

    Args:
        left: One element's short and long extent, in model millimetres.
        right: The other's.

    Returns:
        True when both extents agree to within 2 per cent.
    """
    return all(
        abs(a - b) <= max(SIZE_ATOL_MM, SIZE_RTOL * max(a, b))
        for a, b in zip(left, right, strict=True)
    )


def drawn_elements(fixture: str, label: PageLabel) -> list[Drawn]:
    """Pair every element of a label with the viewport that places it.

    Args:
        fixture: The fixture the label was read from.
        label: The page label to walk.

    Returns:
        One :class:`Drawn` per element that names a viewport the label declares.
        Elements placed in no viewport are left out: without one there is no scale, no
        plane and nothing to measure against.
    """
    viewports = {viewport.local_id: viewport for viewport in label.viewports or []}
    model = label.source_model
    items: list[Drawn] = []
    for element in label.elements or []:
        viewport = viewports.get(element.viewport or "")
        if viewport is None:
            continue
        items.append(
            Drawn(
                fixture=fixture,
                sheet_id=label.sheet.sheet_id,
                element=element,
                viewport=viewport,
                length_unit=model.length_unit if model else None,
                sha256=model.sha256 if model else None,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Reading a fixture
# ---------------------------------------------------------------------------
def read_fixture(path: Path) -> tuple[bytes | None, list[str]]:
    """Read one fixture as bytes, checking what only bytes can show.

    The canonical form is a statement about a file and not about a parse tree, and
    :meth:`pathlib.Path.read_text` applies universal-newline translation: a fixture
    saved with CRLF comes back with LF and compares equal to a canonical one. Every
    check of the canonical form therefore starts here, with the bytes on disk.

    Args:
        path: The fixture's path.

    Returns:
        The bytes and the defects found in them, or None and a message when the file is
        missing or is not UTF-8 at all.
    """
    if not path.is_file():
        return (None, ["is listed in the manifest but missing from the fixture tree"])
    raw = path.read_bytes()
    found: list[str] = []
    if raw.startswith(codecs.BOM_UTF8):
        found.append("begins with a UTF-8 byte-order mark; canonical JSON carries none")
    if b"\r" in raw:
        found.append("uses a CR line ending; canonical JSON is LF throughout")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return (None, [*found, f"is not valid UTF-8: {error}"])
    return (raw, found)


def parse_fixture(raw: bytes) -> tuple[Any, list[str]]:
    """Parse a fixture's bytes as JSON.

    Args:
        raw: The bytes read from disk.

    Returns:
        The parsed document, or None and a message when it is not JSON. A document that
        does not parse cannot be validated, so the caller stops there.
    """
    try:
        return (json.loads(raw.decode("utf-8")), [])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return (None, [f"is not parseable JSON: {error}"])


def canonical_text_bytes(document: Any) -> bytes:  # noqa: ANN401
    """Return a parsed document re-serialised in the canonical form.

    This is the form an invalid fixture is held to: no model can load it, so its
    canonical form cannot be checked by round-tripping it through one.

    Args:
        document: The parsed document.

    Returns:
        Canonical JSON text as UTF-8 bytes, ending in exactly one newline.
    """
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    return f"{text}\n".encode()


def expect_model_rejects(raw: bytes, loader: Callable[[bytes], BaseModel]) -> list[str]:
    """Check that the models refuse a document the schema refuses.

    Args:
        raw: The document's bytes.
        loader: The model loader for this corpus.

    Returns:
        A single message when the models accepted it, and nothing otherwise. The schema
        is the source of truth and the models mirror it or are stricter, so a document
        the schema rejects must never load.
    """
    try:
        loader(raw)
    except (ValidationError, ValueError):
        return []
    return ["is rejected by the schema but accepted by planlabel.model"]


def declared_level(filename: str) -> str | None:
    """Return the conformance level a fixture's filename claims.

    Args:
        filename: The bare filename, such as ``04-l3-schalplan-multi-viewport.json``.

    Returns:
        ``L1``, ``L2`` or ``L3``, or None when the name claims nothing.
    """
    match = LEVEL_IN_NAME_RE.search(Path(filename).stem)
    return match.group(1).upper() if match else None


# ---------------------------------------------------------------------------
# Per-file drivers: page labels
# ---------------------------------------------------------------------------
def schema_findings(document: Any, kind: DocumentKind) -> list[str]:  # noqa: ANN401
    """Validate one fixture against its schema, through the validator.

    The rule "a document must validate against its schema" has one implementation, in
    :func:`planlabel.validate.schema.check_schema`, and this is how the corpus reaches
    it. The negative fixtures below do keep their own ``jsonschema`` validator, and
    that is not the same rule: they are checking the *manifest's* claim about which
    keyword fails and where, which needs the raw ``ValidationError`` and is a statement
    about the corpus rather than about the format.

    Args:
        document: The parsed fixture.
        kind: Which schema to hold it to.

    Returns:
        One message per violation, each carrying the rule's code.
    """
    return [
        f"[{item.code} {item.severity.value}] at {item.path or '/'}: {item.message}"
        for item in check_schema(document, kind, source="fixture")
    ]


def validator_findings(findings: list[Finding]) -> list[str]:
    """Render what the validator found, for this script's report.

    Warnings count as problems here, unlike on the command line, where a label with
    warnings and no errors is conforming and exits 0. The difference is deliberate and
    is about what this corpus is for: these twelve labels are what a reader copies when
    learning the format, so a fixture that trips a SHOULD teaches the SHOULD wrongly. A
    warning fired by a real drawing is a question; a warning fired by the reference
    corpus is a defect in the reference corpus.

    Args:
        findings: What :func:`planlabel.validate.check_page_document` or
            :func:`planlabel.validate.check_page_label` returned.

    Returns:
        One message per finding, each carrying the rule's stable code.
    """
    return [
        f"[{item.code} {item.severity.value}] {item.message}"
        for item in sorted(findings, key=lambda item: item.sort_key)
    ]


def check_valid_label(
    root: Path,
    entry: dict[str, Any],
) -> tuple[list[Problem], PageLabel | None]:
    """Run every check over one page label that is expected to be valid.

    Args:
        root: The ``labels`` fixture directory.
        entry: The manifest entry describing the file.

    Returns:
        One problem per failed expectation, and the loaded label when it loaded, for
        the corpus-wide checks to use.
    """
    name = str(entry["file"])
    where = f"labels/{name}"
    raw, found = read_fixture(root / name)
    if raw is None:
        return ([Problem(where, message) for message in found], None)
    document, parse_errors = parse_fixture(raw)
    if document is None:
        return ([Problem(where, message) for message in found + parse_errors], None)
    schema_errors = schema_findings(document, DocumentKind.PAGE)
    if schema_errors:
        return ([Problem(where, message) for message in found + schema_errors], None)
    # Before the model is built: two rules the model refuses to let a label exist with,
    # which would otherwise reach this script only as "it would not load".
    found += validator_findings(check_page_document(document, source="fixture"))
    try:
        label = load_page_label(raw)
    except (ValidationError, ValueError) as error:
        found.append(f"the schema accepts it but planlabel.model does not: {error}")
        return ([Problem(where, message) for message in found], None)
    if canonical_bytes(label) != raw:
        found.append("does not round-trip byte for byte through the models")
    level = conformance_level(label).value
    if level != entry.get("level"):
        found.append(f"reaches {level}, but the manifest says {entry.get('level')}")
    if declared_level(name) not in (None, level):
        found.append(f"reaches {level}, but the filename says {declared_level(name)}")
    found.extend(validator_findings(check_page_label(label, source="fixture")))
    for check in LABEL_CHECKS:
        found.extend(check(label))
    return ([Problem(where, message) for message in found], label)


def check_invalid_label(
    root: Path,
    entry: dict[str, Any],
    validator: Draft202012Validator,
) -> list[Problem]:
    """Run every check over one page label that is expected to fail, in exactly one way.

    Args:
        root: The ``labels`` fixture directory.
        entry: The manifest entry describing the file.
        validator: A validator built from the page schema.

    Returns:
        One problem per failed expectation.
    """
    return check_invalid_document(
        f"labels/{entry['file']}", root, entry, validator, load_page_label
    )


def check_invalid_document(
    where: str,
    root: Path,
    entry: dict[str, Any],
    validator: Draft202012Validator,
    loader: Callable[[bytes], BaseModel],
) -> list[Problem]:
    """Run every check over one document that is expected to fail, in exactly one way.

    Args:
        where: How to name the fixture in a message.
        root: The fixture directory of this corpus.
        entry: The manifest entry describing the file.
        validator: A validator built from this corpus's schema.
        loader: The model loader for this corpus.

    Returns:
        One problem per failed expectation.
    """
    raw, found = read_fixture(root / str(entry["file"]))
    if raw is None:
        return [Problem(where, message) for message in found]
    document, parse_errors = parse_fixture(raw)
    found += parse_errors
    if document is None:
        return [Problem(where, message) for message in found]
    errors = list(validator.iter_errors(document))
    if len(errors) != 1:
        keywords = ", ".join(sorted(str(error.validator) for error in errors))
        found.append(f"produces {len(errors)} schema errors ({keywords or 'none'}), expected 1")
    else:
        found.extend(compare_error(errors[0], entry))
    if canonical_text_bytes(document) != raw:
        found.append("is not canonical JSON: sorted keys, two-space indent, one trailing newline")
    found.extend(expect_model_rejects(raw, loader))
    return [Problem(where, message) for message in found]


def compare_error(error: Any, entry: dict[str, Any]) -> list[str]:  # noqa: ANN401
    """Compare the single schema error a fixture produced with what the manifest states.

    Args:
        error: The ``jsonschema.exceptions.ValidationError`` the document produced.
        entry: The manifest entry describing the file.

    Returns:
        One message per field that disagrees.
    """
    found: list[str] = []
    if error.validator != entry.get("keyword"):
        found.append(
            f"fails on {error.validator!r}, but the manifest says {entry.get('keyword')!r}"
        )
    if error.json_path != entry.get("errorPath"):
        found.append(
            f"fails at {error.json_path!r}, but the manifest says {entry.get('errorPath')!r}"
        )
    rendered = "#/" + "/".join(str(part) for part in error.absolute_schema_path)
    if rendered != entry.get("validatorSchemaPath"):
        found.append(
            f"validator schema path is {rendered!r}, but the manifest says "
            f"{entry.get('validatorSchemaPath')!r}"
        )
    return found


# ---------------------------------------------------------------------------
# Per-file drivers: the document-level index
# ---------------------------------------------------------------------------
def check_valid_index(
    root: Path,
    entry: dict[str, Any],
) -> tuple[list[Problem], LabelIndex | None]:
    """Run every check over one index that is expected to be valid.

    Args:
        root: The ``index`` fixture directory.
        entry: The manifest entry describing the file.

    Returns:
        One problem per failed expectation, and the loaded index when it loaded.
    """
    name = str(entry["file"])
    where = f"index/{name}"
    raw, found = read_fixture(root / name)
    if raw is None:
        return ([Problem(where, message) for message in found], None)
    document, parse_errors = parse_fixture(raw)
    if document is None:
        return ([Problem(where, message) for message in found + parse_errors], None)
    schema_errors = schema_findings(document, DocumentKind.INDEX)
    if schema_errors:
        return ([Problem(where, message) for message in found + schema_errors], None)
    try:
        index = load_label_index(raw)
    except (ValidationError, ValueError) as error:
        found.append(f"the schema accepts it but planlabel.model does not: {error}")
        return ([Problem(where, message) for message in found], None)
    if canonical_bytes(index) != raw:
        found.append("does not round-trip byte for byte through the models")
    return ([Problem(where, message) for message in found], index)


def check_index_against_labels(
    where: str,
    index: LabelIndex,
    labels: dict[str, PageLabel],
) -> list[Problem]:
    """Check that an index says about a sheet what that sheet's label says about itself.

    An index exists so that a reader can see what is labelled, and at which level,
    without opening every page attachment. That is only worth anything while the two
    agree: a level recorded here that the label does not reach is a promise the
    attachment does not keep, and SPEC 4.5 makes the page index a MUST.

    Args:
        where: How to name the index fixture in a message.
        index: The loaded index.
        labels: Every valid page label of this corpus, by the sheet id it prints.

    Returns:
        One problem per entry that contradicts the label it describes.
    """
    found: list[Problem] = []
    for page in index.pages:
        label = labels.get(page.sheet_id)
        if label is None:
            continue
        level = conformance_level(label).value
        if page.level.value != level:
            found.append(
                Problem(
                    where,
                    f"page {page.page_index} ({page.sheet_id}) is recorded as "
                    f"{page.level.value}, but its label reaches {level}",
                )
            )
        if page.page_index != label.page.index:
            found.append(
                Problem(
                    where,
                    f"sheet {page.sheet_id} is indexed at page {page.page_index}, but its "
                    f"label declares page.index {label.page.index}",
                )
            )
        found += index_entry_text(where, page, label)
    return found


def index_entry_text(where: str, page: Any, label: PageLabel) -> list[Problem]:  # noqa: ANN401
    """Check the title and revision an index entry copies from a sheet.

    Args:
        where: How to name the index fixture in a message.
        page: The index entry.
        label: The page label it describes.

    Returns:
        One problem per member that is present and disagrees. A member the index omits
        is not a disagreement: an index may be terser than the sheets it lists.
    """
    found: list[Problem] = []
    for member, indexed, printed in (
        ("title", page.title, label.sheet.title),
        ("revision", page.revision, label.sheet.revision),
    ):
        if indexed is not None and indexed != printed:
            found.append(
                Problem(
                    where,
                    f"sheet {page.sheet_id} is indexed with {member} {indexed!r}, but its "
                    f"label prints {printed!r}",
                )
            )
    return found


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------
def check_schema_pointers(
    corpus: str,
    schema: dict[str, Any],
    entries: list[dict[str, Any]],
) -> list[Problem]:
    """Check that every manifest ``schemaPointer`` dereferences inside the schema.

    Args:
        corpus: ``labels`` or ``index``, for the message.
        schema: The parsed schema of this corpus.
        entries: The manifest's ``invalid`` entries.

    Returns:
        One problem per pointer that does not resolve.
    """
    found: list[Problem] = []
    for entry in entries:
        pointer = str(entry.get("schemaPointer", ""))
        node: Any = schema
        for part in pointer.removeprefix("#/").split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                found.append(
                    Problem(
                        f"{corpus}/manifest.json", f"schemaPointer {pointer!r} does not resolve"
                    )
                )
                break
    return found


def check_manifest_matches_disk(corpus: str, root: Path, manifest: dict[str, Any]) -> list[Problem]:
    """Check that a manifest lists exactly the files on disk, as many of each as agreed.

    Args:
        corpus: ``labels`` or ``index``.
        root: That corpus's fixture directory.
        manifest: Its parsed manifest.

    Returns:
        One problem per file that is listed but missing, present but unlisted, or per
        directory whose count is not the one the Phase 1 gate fixed.
    """
    expected = FIXTURES_PER_DIRECTORY[corpus]
    where = f"{corpus}/manifest.json"
    found: list[Problem] = []
    for kind in ("valid", "invalid"):
        listed = [str(entry["file"]) for entry in manifest[kind]]
        on_disk = sorted(f"{kind}/{path.name}" for path in (root / kind).glob("*.json"))
        found.extend(
            Problem(where, f"lists {name}, which is not on disk")
            for name in sorted(set(listed) - set(on_disk))
        )
        found.extend(
            Problem(where, f"does not list {name}, which is on disk")
            for name in sorted(set(on_disk) - set(listed))
        )
        if len(on_disk) != expected:
            found.append(Problem(where, f"{kind}/ holds {len(on_disk)} files, expected {expected}"))
        if listed != sorted(listed):
            found.append(Problem(where, f"{kind} entries are not in filename order"))
    return found


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------
#: The corpus checks that run over one loaded page label on its own.
#:
#: Every rule the validator owns is applied by :func:`validator_findings` instead, and
#: none of them appears here. What is left is what is about this corpus rather than
#: about the format: whether its GlobalIds and sheet sizes are real, whether a level
#: agrees with the transform of the viewport it is drawn in, whether a section mark
#: lies on the plane of the section it opens, and whether a tag prints the property it
#: says it shows.
LABEL_CHECKS = (
    check_identifiers_and_page_size,
    check_levels,
    check_section_marks,
    check_shown_properties,
)


def run_labels(root: Path) -> tuple[list[Problem], list[tuple[str, PageLabel]]]:
    """Check the page-label corpus.

    Args:
        root: The ``labels`` fixture directory.

    Returns:
        Every problem found, and every label that loaded, paired with its fixture name.
    """
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    schema = page_schema()
    validator = Draft202012Validator(schema)
    problems = check_manifest_matches_disk("labels", root, manifest)
    problems += check_schema_pointers("labels", schema, manifest["invalid"])
    loaded: list[tuple[str, PageLabel]] = []
    for entry in manifest["valid"]:
        found, label = check_valid_label(root, entry)
        problems += found
        if label is not None:
            loaded.append((f"labels/{entry['file']}", label))
    for entry in manifest["invalid"]:
        problems += check_invalid_label(root, entry, validator)
    return (problems, loaded)


def run_index(
    root: Path,
    labels: list[tuple[str, PageLabel]],
) -> tuple[list[Problem], list[tuple[str, LabelIndex]]]:
    """Check the document-index corpus, including against the labels it describes.

    Args:
        root: The ``index`` fixture directory.
        labels: Every valid page label, paired with its fixture name.

    Returns:
        Every problem found, and every index that loaded, paired with its fixture name.
    """
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    schema = index_schema()
    validator = Draft202012Validator(schema)
    problems = check_manifest_matches_disk("index", root, manifest)
    problems += check_schema_pointers("index", schema, manifest["invalid"])
    by_sheet = {label.sheet.sheet_id: label for _, label in labels}
    loaded: list[tuple[str, LabelIndex]] = []
    for entry in manifest["valid"]:
        found, index = check_valid_index(root, entry)
        problems += found
        if index is not None:
            where = f"index/{entry['file']}"
            problems += check_index_against_labels(where, index, by_sheet)
            loaded.append((where, index))
    for entry in manifest["invalid"]:
        problems += check_invalid_document(
            f"index/{entry['file']}", root, entry, validator, load_label_index
        )
    return (problems, loaded)


def run_corpus_rules(
    labels: list[tuple[str, PageLabel]],
    indexes: list[tuple[str, LabelIndex]],
) -> list[Problem]:
    """Run every rule that needs more than one document, or more than the schema.

    Args:
        labels: Every valid page label, paired with its fixture name.
        indexes: Every valid index, paired with its fixture name.

    Returns:
        Every problem found: model identity and cross-sheet size across the corpus,
        stated sizes and physical plausibility within each label.
    """
    declared: list[tuple[str, Model | None]] = [
        (fixture, label.source_model) for fixture, label in labels
    ]
    declared += [(fixture, index.source_model) for fixture, index in indexes]
    declarations = [
        ModelRef(fixture, model.sha256, model.file, model.length_unit, model.ifc_schema)
        for fixture, model in declared
        if model is not None
    ]
    problems: list[Problem] = []
    drawn: list[Drawn] = []
    for fixture, label in labels:
        items = drawn_elements(fixture, label)
        drawn += items
        for item in items:
            problems += [Problem(fixture, message) for message in check_stated_sizes(item)]
        problems += check_foundations(fixture, label)
        problems += check_slab_thickness(fixture, label)
        problems += check_storey_heights(fixture, label)
    return problems + check_model_identity(declarations) + check_cross_sheet_sizes(drawn)


def run(root: Path) -> list[Problem]:
    """Run every check over a fixture tree.

    Args:
        root: The directory holding ``labels/`` and ``index/``.

    Returns:
        Every problem found: per-file ones in file order, then the ones that belong to
        no single file.
    """
    problems, labels = run_labels(root / "labels")
    index_problems, indexes = run_index(root / "index", labels)
    return problems + index_problems + run_corpus_rules(labels, indexes)


def count_fixtures(root: Path) -> int:
    """Count the documents a run examined.

    Args:
        root: The fixture tree's root.

    Returns:
        How many JSON fixtures lie under ``labels/`` and ``index/``, manifests aside.
    """
    return sum(
        len(list((root / corpus / kind).glob("*.json")))
        for corpus in FIXTURES_PER_DIRECTORY
        for kind in ("valid", "invalid")
    )


def report(problems: Iterable[Problem]) -> None:
    """Print every problem, grouped by the fixture it was found in.

    Args:
        problems: The problems, in the order the run produced them.
    """
    grouped: dict[str, list[str]] = {}
    for problem in problems:
        grouped.setdefault(problem.where, []).append(problem.what)
    for where, messages in grouped.items():
        print(where)
        for message in messages:
            print(f"  {message}")


def main(argv: Sequence[str]) -> int:
    """Check the fixture tree and report.

    Args:
        argv: Command-line arguments after the program name. An optional first argument
            overrides the fixture root, which defaults to the one in this repository.

    Returns:
        0 when every check passed, and 1 otherwise.
    """
    default = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    root = Path(argv[0]) if argv else default
    problems = run(root)
    report(problems)
    checked = count_fixtures(root)
    if problems:
        print(f"\n{len(problems)} problem(s) in {checked} fixture(s)")
        return 1
    print(
        f"{checked} fixtures pass: manifests, canonical bytes and levels here; schema, "
        f"references, geometry and provenance through planlabel.validate; plus drawing "
        f"sense, model identity and stated sizes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
