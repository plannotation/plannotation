# SPDX-License-Identifier: Apache-2.0
"""Rule 3: the geometric rules of section 3, and which of them are errors.

Section 4.4 (6) lists what a validator must report: bounding-box ordering (3.4),
geometry within its bounding box (3.4), bounding boxes within the page, page dimensions
matching the page (3.1), a singular or ``scale``-contradicting ``paperToPlane`` (3.5),
and non-orthonormal plane axes (3.6). Each of those is a MUST, and each is an error
here. Three further rules are checked and are not on that list; their severities are
decided in :mod:`plannotation.validate.codes` and summarised again below, because this is
where the reasoning has to be right.

Which box is "the page"
-----------------------
A bbox must lie inside the page, and the obvious reading of "the page" in a PDF is the
**media box**. Section 3.1 of the specification says the page is the **CropBox**,
falling back to the MediaBox where there is none, and 1.2's governing principle is why:
the CropBox is the page as displayed and printed, so a plannotation measures the page a
person measures. Taking the media box instead would let a plannotation place a bounding
box in the margin a viewer crops away, where nothing is drawn and nobody can find it.

In practice the rule is checked in two halves, which is what makes the difference
between the two boxes moot for most documents:

* **PL-GEO-003** holds every bounding box to the page the *plannotation declares*,
  the rectangle from ``(0, 0)`` to ``(page.widthMm, page.heightMm)``. This is the half
  that works with no document in front of the validator.
* **PL-GEO-001** holds that declared page to the document, through
  :func:`plannotation.pdf.embed.page_geometry`, which already implements 3.1 exactly:
  CropBox clipped to MediaBox, falling back to MediaBox, honouring ``/UserUnit``.

So the box a coordinate is ultimately held to is the CropBox, and the validator says so
in one place rather than re-deriving the page in each rule.

The three rules that are not errors
-----------------------------------
* **PL-GEO-005**, an item's bbox outside its viewport's, is a warning because 4.4 names
  that exact case as its example of a warning, and because a tag in the margin beside a
  viewport is ordinary drafting.
* **PL-GEO-009**, a dimension's value against the length it is drawn at, is a warning
  because it rests on a tolerance and on the assumption that the dimension line is
  drawn to the full run it measures. A dimension drawn broken, or in a detail marked
  not to scale, breaks that assumption legitimately, and 4.4 files a finding that rests
  on a tolerance or a heuristic as a warning.
* **PL-GEO-013**, ``paperToPlane`` without ``model.lengthUnit``, *is* an error: 3.5
  states it as a writer MUST, and without the unit a reader may not report any distance
  derived from the transform as a length, which is most of what a transform is for.

Relations among rounded numbers
-------------------------------
Section 3.8 rounds every number to three decimals, and four rules here test numbers
for an exact relation: a plane axis of unit length (PL-GEO-010), two axes at right
angles (PL-GEO-011), ``scale`` against the transform (PL-GEO-008), and the plane
origin against ``storey.elevation + cutHeight`` (PL-GEO-012). An oblique axis has no
exact three-decimal spelling: a plan turned 29.18 degrees to follow its building's
grid has ``xAxis`` ``[0.873, -0.487, 0]``, which is 0.99965 long. Each of those rules
therefore allows for everything rounding can explain, by a bound derived from
:data:`ROUNDING`, the most that rounding moves one number, and by nothing more.

The determinant rules and the bounding-box rules are not relaxed. A reader inverts the
transform the file holds, so its determinant is a fact about the file; and rounding is
monotonic, so it can neither reverse a box nor move one out of a box that held it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from plannotation.errors import PlannotationMismatchError
from plannotation.pdf.embed import PAGE_DIMENSION_TOLERANCE_MM, check_plannotations_against
from plannotation.units import COORD_DECIMALS
from plannotation.validate.codes import finding
from plannotation.validate.geometry import (
    contains,
    cross,
    determinant,
    dot,
    is_similarity,
    norm,
    polyline_bbox,
    polyline_length,
)
from plannotation.validate.schema import json_pointer

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pikepdf import Pdf

    from plannotation.model import Annotation, Element, Plannotation, Viewport
    from plannotation.validate.report import Finding

__all__ = [
    "AXIS_TOLERANCE",
    "CUT_HEIGHT_TOLERANCE",
    "GEOMETRIC_TOLERANCE_MM",
    "LENGTH_TOLERANCE_MM",
    "LENGTH_TOLERANCE_RATIO",
    "METRES_PER_MODEL_UNIT",
    "MM_PER_MODEL_UNIT",
    "ORTHOGONALITY_TOLERANCE",
    "ROUNDING",
    "SCALE_ROUNDING",
    "SCALE_TOLERANCE_RATIO",
    "check_geometry",
    "check_page_against_document",
    "declared_length",
    "measured_length_mm",
    "millimetres",
]

#: The geometric tolerance of section 3.1, in paper millimetres.
#:
#: Deliberately the same object as the PDF carrier's page-dimension tolerance rather
#: than a second constant holding the same number. Both come from the one sentence in
#: 3.1 that fixes it, and two constants that happen to agree today are two constants
#: that can stop agreeing.
GEOMETRIC_TOLERANCE_MM: Final = PAGE_DIMENSION_TOLERANCE_MM

#: The most that serialising moves one number: half a unit in the last of the three
#: decimals section 3.8 keeps. Every tolerance on a relation among serialised numbers
#: below is derived from it.
ROUNDING: Final = 0.5 * 10.0**-COORD_DECIMALS

#: How far a plane axis may be from unit length -- section 3.6.
#:
#: Rounding each of three components by up to :data:`ROUNDING` moves a vector by at most
#: ``sqrt(3) * ROUNDING``, and changes its length by no more than it moves it. An axis
#: along a model axis gains nothing from this: the nearest three-decimal lengths either
#: side of 1 are 0.001 away, which is outside it.
AXIS_TOLERANCE: Final = math.sqrt(3.0) * ROUNDING

#: How far the dot product of a plane's axes may be from zero -- section 3.6.
#:
#: Unit axes ``u`` and ``v`` with ``u . v = 0``, moved by rounding to ``u + e`` and
#: ``v + f``, have the dot product ``u . f + e . v + e . f``. Each of ``e`` and ``f`` is
#: at most ``sqrt(3) * ROUNDING`` long, so that is at most
#: ``2 * sqrt(3) * ROUNDING + 3 * ROUNDING**2``.
ORTHOGONALITY_TOLERANCE: Final = 2.0 * math.sqrt(3.0) * ROUNDING + 3.0 * ROUNDING**2

#: How far ``paperToPlane`` and ``scale`` may disagree -- section 3.5's 0.1 per cent,
#: before rounding is allowed for.
SCALE_TOLERANCE_RATIO: Final = 1e-3

#: How far rounding may move the ``k`` a scale is recovered from, in model length units
#: per paper millimetre -- section 3.5.
#:
#: Rounding the four coefficients of the linear part by up to :data:`ROUNDING` each adds
#: an error of spectral norm at most ``2 * ROUNDING``. That bounds how far each singular
#: value moves, and ``k = sqrt(|det|)`` lies between the two. It is 1 in the scale
#: denominator for a model in metres and 0.001 for one in millimetres: an unrotated
#: transform at a standard scale is exact and loses nothing, and a rotated one in metres
#: is not, because its coefficients carry a sine and a cosine.
SCALE_ROUNDING: Final = 2.0 * ROUNDING

#: How far ``plane.origin`` along the view normal may be from ``storey.elevation +
#: cutHeight``, in model length units -- section 3.6.
#:
#: Rounding moves the elevation and the cut height by up to :data:`ROUNDING` each, and
#: the origin's component along the unit normal ``n`` by up to
#: ``ROUNDING * (|nx| + |ny| + |nz|)``, which is at most ``sqrt(3) * ROUNDING``. On a
#: plan the normal is the z axis and all three numbers are multiples of 0.001, so a
#: disagreement of 0.001 passes and one of 0.002 does not.
CUT_HEIGHT_TOLERANCE: Final = (2.0 + math.sqrt(3.0)) * ROUNDING

#: Relative tolerance on a dimension's value against the length it is drawn at.
LENGTH_TOLERANCE_RATIO: Final = 0.01

#: Absolute floor of that tolerance, in model millimetres.
LENGTH_TOLERANCE_MM: Final = 0.5

#: Millimetres per value of ``model.lengthUnit``.
MM_PER_MODEL_UNIT: Final = {"m": 1000.0, "cm": 10.0, "mm": 1.0}

#: Metres per value of ``model.lengthUnit``.
METRES_PER_MODEL_UNIT: Final = {"m": 1.0, "cm": 0.01, "mm": 0.001}

#: Metres per value of ``annotation.unit``, for the values that are lengths.
_METRES_PER_MEASURE: Final = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def check_geometry(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Run every geometric rule that needs only the plannotation itself.

    The page dimensions are not checked here: that rule needs the document, and lives
    in :func:`check_page_against_document`.

    Args:
        plannotation: The loaded plannotation.
        source: Which document it is, for the findings.

    Returns:
        Every violation, in document order.
    """
    return [
        *_check_bboxes(plannotation, source=source),
        *_check_drawn_geometry(plannotation, source=source),
        *_check_viewport_containment(plannotation, source=source),
        *_check_transforms(plannotation, source=source),
        *_check_plane_axes(plannotation, source=source),
        *_check_cut_heights(plannotation, source=source),
        *_check_dimensions(plannotation, source=source),
    ]


def check_page_against_document(
    pdf: Pdf, plannotation: Plannotation, *, source: str
) -> list[Finding]:
    """Check a plannotation's ``page`` block against the page it describes.

    The comparison is delegated to :func:`plannotation.pdf.embed.check_plannotations_against`,
    which is the same check ``attach`` makes before it writes anything. That is
    deliberate: a validator that disagreed with the writer about whether a plannotation
    fits a page would be worse than either being wrong on its own. One code covers
    dimensions and rotation together because one call decides both; the message says
    which fired.

    The caller must have established that ``plannotation.page.index`` is a page of
    ``pdf``, which is :func:`plannotation.validate.carrier.check_page_identity`; this
    function would otherwise report that as a page mismatch, under the wrong code.

    Args:
        pdf: The open document.
        plannotation: The plannotation, which describes the page its ``page.index``
            names.
        source: Which document it is, for the findings.

    Returns:
        A single finding when the plannotation contradicts the page, and nothing when it
        does not.
    """
    try:
        check_plannotations_against(pdf, [plannotation])
    except PlannotationMismatchError as exc:
        return [finding("PL-GEO-001", message=str(exc), path="/page", source=source)]
    return []


# ---------------------------------------------------------------------------
# Bounding boxes
# ---------------------------------------------------------------------------
def _named_bboxes(plannotation: Plannotation) -> Iterator[tuple[str, str, Sequence[float]]]:
    """Yield every bbox in a plannotation with a name and a JSON Pointer for it.

    Args:
        plannotation: The plannotation to walk.

    Yields:
        Triples of a human-readable name, a JSON Pointer and the bbox.
    """
    if plannotation.sheet.title_block_bbox is not None:
        yield ("sheet.titleBlockBBox", "/sheet/titleBlockBBox", plannotation.sheet.title_block_bbox)
    for position, viewport in enumerate(plannotation.viewports or []):
        name = f"viewport {viewport.local_id!r}"
        yield (name, json_pointer(["viewports", position, "paperBBox"]), viewport.paper_bbox)
    for position, element in enumerate(plannotation.elements or []):
        name = f"element {element.local_id!r}"
        yield (name, json_pointer(["elements", position, "paperBBox"]), element.paper_bbox)
    for position, annotation in enumerate(plannotation.annotations or []):
        name = f"annotation {annotation.local_id!r}"
        yield (name, json_pointer(["annotations", position, "paperBBox"]), annotation.paper_bbox)


def _check_bboxes(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check that every bbox is ordered and lies on the page the plannotation declares.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        One finding per bbox that is out of order, and one per bbox that leaves the
        page. A box that is out of order is not also tested for containment: the test
        would be meaningless on a rectangle whose corners are the wrong way round.
    """
    page = (0.0, 0.0, plannotation.page.width_mm, plannotation.page.height_mm)
    found: list[Finding] = []
    for name, path, box in _named_bboxes(plannotation):
        if box[0] > box[2] or box[1] > box[3]:
            found.append(
                finding(
                    "PL-GEO-002",
                    message=(
                        f"{name}: bbox {list(box)} is not ordered x0 <= x1, y0 <= y1; "
                        f"a bbox is [x0, y0, x1, y1] with the lower-left corner first"
                    ),
                    path=path,
                    source=source,
                )
            )
            continue
        if not contains(page, box, tolerance=GEOMETRIC_TOLERANCE_MM):
            found.append(
                finding(
                    "PL-GEO-003",
                    message=(
                        f"{name}: bbox {list(box)} leaves the "
                        f"{plannotation.page.width_mm:g} x {plannotation.page.height_mm:g} mm page "
                        f"by more than the {GEOMETRIC_TOLERANCE_MM:g} mm tolerance"
                    ),
                    path=path,
                    source=source,
                )
            )
    return found


def _check_drawn_geometry(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check that drawn geometry stays inside the bbox of the item carrying it.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        One finding per annotation geometry and per element outline that escapes its
        own bounding box by more than the geometric tolerance.
    """
    found: list[Finding] = []
    for position, annotation in enumerate(plannotation.annotations or []):
        if annotation.geometry is None:
            continue
        span = polyline_bbox(annotation.geometry)
        if contains(annotation.paper_bbox, span, tolerance=GEOMETRIC_TOLERANCE_MM):
            continue
        found.append(
            finding(
                "PL-GEO-004",
                message=(
                    f"annotation {annotation.local_id!r}: geometry spans {list(span)}, "
                    f"outside its bbox {list(annotation.paper_bbox)}"
                ),
                path=json_pointer(["annotations", position, "geometry"]),
                source=source,
            )
        )
    for position, element in enumerate(plannotation.elements or []):
        for outline_index, outline in enumerate(element.paper_outlines or []):
            span = polyline_bbox(outline)
            if contains(element.paper_bbox, span, tolerance=GEOMETRIC_TOLERANCE_MM):
                continue
            found.append(
                finding(
                    "PL-GEO-004",
                    message=(
                        f"element {element.local_id!r}: outline {outline_index} spans "
                        f"{list(span)}, outside its bbox {list(element.paper_bbox)}"
                    ),
                    path=json_pointer(["elements", position, "paperOutlines", outline_index]),
                    source=source,
                )
            )
    return found


def _check_viewport_containment(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check that items sit inside the viewport they name.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        One warning per item whose bbox leaves its viewport's. An item naming a
        viewport that does not exist is left to PL-REF-002, which is the rule for that.
    """
    viewports = {viewport.local_id: viewport for viewport in plannotation.viewports or []}
    found: list[Finding] = []
    collections: tuple[tuple[str, Sequence[Element | Annotation]], ...] = (
        ("elements", plannotation.elements or []),
        ("annotations", plannotation.annotations or []),
    )
    for collection, items in collections:
        for position, item in enumerate(items):
            viewport = viewports.get(item.viewport or "")
            if viewport is None:
                continue
            if contains(viewport.paper_bbox, item.paper_bbox, tolerance=GEOMETRIC_TOLERANCE_MM):
                continue
            found.append(
                finding(
                    "PL-GEO-005",
                    message=(
                        f"{collection[:-1]} {item.local_id!r}: bbox "
                        f"{list(item.paper_bbox)} is not inside viewport "
                        f"{item.viewport!r} {list(viewport.paper_bbox)}; legitimate for "
                        f"something drawn in the margin beside the view"
                    ),
                    path=json_pointer([collection, position, "paperBBox"]),
                    source=source,
                )
            )
    return found


# ---------------------------------------------------------------------------
# Transforms and planes
# ---------------------------------------------------------------------------
def _check_transforms(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check every ``paperToPlane`` for invertibility, handedness and scale.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        Every violation. A transform that is singular or mirrored is not then compared
        with ``scale``: the comparison would report a second failure caused by the
        first.
    """
    unit = plannotation.source_model.length_unit if plannotation.source_model else None
    found: list[Finding] = []
    without_unit: list[tuple[str, str]] = []
    for position, viewport in enumerate(plannotation.viewports or []):
        matrix = viewport.paper_to_plane
        if matrix is None:
            continue
        path = json_pointer(["viewports", position, "paperToPlane"])
        det = determinant(matrix)
        if det == 0.0:
            found.append(
                finding(
                    "PL-GEO-006",
                    message=(
                        f"viewport {viewport.local_id!r}: paperToPlane {list(matrix)} has "
                        f"determinant 0, which collapses the viewport to a line and "
                        f"cannot be inverted"
                    ),
                    path=path,
                    source=source,
                )
            )
            continue
        if det < 0.0:
            found.append(
                finding(
                    "PL-GEO-007",
                    message=(
                        f"viewport {viewport.local_id!r}: paperToPlane {list(matrix)} has "
                        f"determinant {det:g}, which mirrors the view; orientation is "
                        f"already carried by plane.xAxis and plane.yAxis"
                    ),
                    path=path,
                    source=source,
                )
            )
            continue
        if unit is None:
            without_unit.append((viewport.local_id, json_pointer(["viewports", position])))
            continue
        found += _check_scale(viewport, matrix, det, unit=unit, path=path, source=source)
    if without_unit:
        named = ", ".join(repr(local_id) for local_id, _ in without_unit)
        found.append(
            finding(
                "PL-GEO-013",
                message=(
                    f"viewport(s) {named} carry paperToPlane, but the plannotation declares "
                    f"no model.lengthUnit; the transform's output is in the model's length "
                    f"unit, and a reader that does not know it may not report any "
                    f"distance derived from the transform as a length"
                ),
                path=without_unit[0][1],
                source=source,
            )
        )
    return found


def _check_scale(
    viewport: Viewport,
    matrix: Sequence[float],
    det: float,
    *,
    unit: str,
    path: str,
    source: str,
) -> list[Finding]:
    """Compare one viewport's ``scale`` with the scale its transform implies.

    Section 3.5 recovers ``k = sqrt(|det|)`` model length units per paper millimetre
    and ``S = k * (millimetres per model length unit)``, and only where the linear part
    is a similarity: where it is not, ``k`` is undefined, ``scale`` stands alone and a
    validator MUST NOT check it against the transform. Both tests allow for rounding: a
    rotated similarity need not round to an exact one, and rounding moves ``k`` by up
    to :data:`SCALE_ROUNDING`.

    Args:
        viewport: The viewport.
        matrix: Its ``paperToPlane``.
        det: That transform's determinant, already known to be positive.
        unit: ``model.lengthUnit``.
        path: The JSON Pointer of the transform.
        source: Which document it is, for the finding.

    Returns:
        A single finding when the two disagree by more than 0.1 per cent plus what
        rounding explains.
    """
    if viewport.scale is None or not is_similarity(matrix, spread=ROUNDING):
        return []
    mm_per_unit = MM_PER_MODEL_UNIT[unit]
    recovered = math.sqrt(det) * mm_per_unit
    tolerance = SCALE_TOLERANCE_RATIO * viewport.scale + SCALE_ROUNDING * mm_per_unit
    if abs(recovered - viewport.scale) <= tolerance:
        return []
    return [
        finding(
            "PL-GEO-008",
            message=(
                f"viewport {viewport.local_id!r}: paperToPlane is a similarity at "
                f"1:{recovered:g} in {unit}, but the viewport declares "
                f"1:{viewport.scale:g}; 3.5 allows them to differ by 0.1 per cent plus "
                f"what three-decimal rounding explains, {tolerance:.3g} here"
            ),
            path=path,
            source=source,
        )
    ]


def _check_plane_axes(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check that every plane's axes are unit vectors and mutually orthogonal.

    Both to within bounds on what rounding to three decimals can do:
    :data:`AXIS_TOLERANCE` and :data:`ORTHOGONALITY_TOLERANCE`.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        One finding per axis that is not of unit length, plus one per pair that is not
        orthogonal.
    """
    found: list[Finding] = []
    for position, viewport in enumerate(plannotation.viewports or []):
        plane = viewport.plane
        if plane is None:
            continue
        base = json_pointer(["viewports", position, "plane"])
        for member, axis in (("xAxis", plane.x_axis), ("yAxis", plane.y_axis)):
            length = norm(axis)
            if abs(length - 1.0) <= AXIS_TOLERANCE:
                continue
            found.append(
                finding(
                    "PL-GEO-010",
                    message=(
                        f"viewport {viewport.local_id!r}: plane {member} {list(axis)} has "
                        f"length {length:.6g}, further from 1 than three-decimal rounding "
                        f"explains ({AXIS_TOLERANCE:.3g}); scale is carried by "
                        f"paperToPlane, so an axis of another length states it a second time"
                    ),
                    path=f"{base}/{member}",
                    source=source,
                )
            )
        product = dot(plane.x_axis, plane.y_axis)
        if abs(product) > ORTHOGONALITY_TOLERANCE:
            found.append(
                finding(
                    "PL-GEO-011",
                    message=(
                        f"viewport {viewport.local_id!r}: plane xAxis {list(plane.x_axis)} "
                        f"and yAxis {list(plane.y_axis)} have dot product {product:.6g}; "
                        f"3.6 requires them to be orthogonal to within what three-decimal "
                        f"rounding explains ({ORTHOGONALITY_TOLERANCE:.3g})"
                    ),
                    path=base,
                    source=source,
                )
            )
    return found


def _check_cut_heights(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check the cut-height convention on every viewport that states one.

    For a cut view the drawing plane is the cutting plane, and ``cutHeight`` is the
    cut's height above ``storey.elevation`` -- German practice's *Schnitthoehe 1,20 m
    ueber OKFF*. Where both are present, the plane origin's component along the view
    normal must be their sum, to within :data:`CUT_HEIGHT_TOLERANCE`, which bounds how
    far rounding the three numbers can separate them.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        One finding per viewport whose plane sits at the wrong height.
    """
    found: list[Finding] = []
    for position, viewport in enumerate(plannotation.viewports or []):
        elevation = viewport.storey.elevation if viewport.storey else None
        plane = viewport.plane
        if viewport.cut_height is None or elevation is None or plane is None:
            continue
        normal = cross(plane.x_axis, plane.y_axis)
        length = norm(normal)
        if length == 0.0:
            continue
        unit_normal = tuple(component / length for component in normal)
        along = dot(plane.origin, unit_normal)
        expected = elevation + viewport.cut_height
        if abs(along - expected) <= CUT_HEIGHT_TOLERANCE:
            continue
        found.append(
            finding(
                "PL-GEO-012",
                message=(
                    f"viewport {viewport.local_id!r}: plane.origin lies {along:g} along "
                    f"the view normal, but storey.elevation + cutHeight is "
                    f"{elevation:g} + {viewport.cut_height:g} = {expected:g}"
                ),
                path=json_pointer(["viewports", position]),
                source=source,
            )
        )
    return found


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------
def millimetres(value: float) -> str:
    """Spell a model length for a message.

    Args:
        value: The length in model millimetres.

    Returns:
        The number with no exponent and no trailing zeros, so that a ten-metre run
        reads 10000 and not 1e+04. Rounded to the three decimals a plannotation is
        serialised at, because a message quoting more precision than the format carries
        invites an argument about a digit that is not in the file.
    """
    return f"{round(value, 3):g}"


def declared_length(annotation: Annotation) -> str:
    """Spell what a dimension says it measures, for a message.

    Args:
        annotation: The dimension, which carries a ``value`` and a ``unit``.

    Returns:
        The value as the sheet prints it, with the millimetres it works out to when
        the unit is not already the millimetre.
    """
    spelled = f"{annotation.value:g} {annotation.unit}"
    converted = measured_length_mm(annotation)
    if converted is None or annotation.unit == "mm":
        return spelled
    return f"{spelled} = {millimetres(converted)} mm"


def measured_length_mm(annotation: Annotation) -> float | None:
    """Return the model length a dimension declares, in millimetres.

    Args:
        annotation: The annotation to read.

    Returns:
        The value converted to millimetres, or None when the annotation is not a
        dimension carrying a length. An angle in degrees and a percentage gradient are
        both dimensions and neither is a length, so neither can be measured off paper.
    """
    if annotation.annotation_type != "dimension" or annotation.value is None:
        return None
    if annotation.unit not in _METRES_PER_MEASURE:
        return None
    return annotation.value * _METRES_PER_MEASURE[annotation.unit] * 1000.0


def _check_dimensions(plannotation: Plannotation, *, source: str) -> list[Finding]:
    """Check that a dimension's value matches the length it is drawn at.

    The drawn model length is the **path length** of the annotation's geometry in paper
    millimetres, taken through its viewport's scale. Measuring the chord instead --
    first point to last -- would pass any dog-legged dimension line whatever, since the
    two agree only for a straight run.

    Args:
        plannotation: The plannotation.
        source: Which document it is, for the findings.

    Returns:
        One warning per dimension whose printed value disagrees with its geometry.
    """
    viewports = {viewport.local_id: viewport for viewport in plannotation.viewports or []}
    found: list[Finding] = []
    for position, annotation in enumerate(plannotation.annotations or []):
        expected = measured_length_mm(annotation)
        viewport = viewports.get(annotation.viewport or "")
        if expected is None or viewport is None or viewport.scale is None:
            continue
        if annotation.geometry is None:
            continue
        paper_mm = polyline_length(annotation.geometry)
        drawn_mm = paper_mm * viewport.scale
        tolerance = max(LENGTH_TOLERANCE_MM, LENGTH_TOLERANCE_RATIO * abs(expected))
        if abs(drawn_mm - expected) <= tolerance:
            continue
        found.append(
            finding(
                "PL-GEO-009",
                message=(
                    f"annotation {annotation.local_id!r}: geometry runs "
                    f"{millimetres(paper_mm)} mm of paper at 1:{viewport.scale:g} = "
                    f"{millimetres(drawn_mm)} mm, but the value is "
                    f"{declared_length(annotation)}"
                ),
                path=json_pointer(["annotations", position]),
                source=source,
            )
        )
    return found
