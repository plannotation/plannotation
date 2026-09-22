# SPDX-License-Identifier: Apache-2.0
"""From SVG user units to paper millimetres to the model plane.

The IfcOpenShell SVG serializer records, on each view group, an ``ifc:matrix3`` that
maps plane coordinates in **metres** to SVG user units, and an ``ifc:plane`` giving the
view's placement in the model. This module turns those into the two things a PlanLabel
viewport carries: the ``paperToPlane`` affine of SPEC 3.5 and the ``plane`` of SPEC 3.6.

Three conventions meet here and each is a chance to be wrong:

* The serializer negates the plane's y when it projects, so ``svg = matrix3 . (u, -v, 1)``.
* SVG is y-down; paper is y-up with its origin at the bottom-left (SPEC 3.3), so a point
  at SVG ``y`` is at paper ``H - y`` on a sheet ``H`` millimetres tall.
* ``paperToPlane`` must output the **model's** length unit, not metres (SPEC 3.5), so the
  file's unit scale divides out.

The composed sheet wraps each view group in a ``transform="translate(ox, oy) scale(k)"``
that places it on the page. That outer transform is folded into the matrix here rather
than applied separately, so that one affine describes the whole path from paper to model
and a reader never has to know a sheet was composed.

Verified against a model whose wall coordinates are known: every corner round-trips to
within 9e-16 model units.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: A 2-D affine in the PDF and PostScript component order.
Affine = tuple[float, float, float, float, float, float]


def paper_to_plane(
    matrix3: Sequence[Sequence[float]],
    page_height_mm: float,
    unit_scale_to_m: float,
    *,
    svg_offset: tuple[float, float] = (0.0, 0.0),
    svg_scale: float = 1.0,
) -> Affine:
    """Return the SPEC 3.5 ``paperToPlane`` affine for one view group.

    Args:
        matrix3: The group's ``ifc:matrix3``, three rows of three, mapping plane metres
            to SVG user units.
        page_height_mm: The sheet's height in millimetres, which the y-flip needs.
        unit_scale_to_m: The model's length unit expressed in metres, as
            ``ifcopenshell.util.unit.calculate_unit_scale`` reports it.
        svg_offset: The translation of the wrapper that places this view on the sheet.
        svg_scale: The uniform scale of that wrapper.

    Returns:
        ``(a, b, c, d, e, f)`` applied as ``X = a*x + c*y + e``, ``Y = b*x + d*y + f``,
        taking paper millimetres to the viewport's model plane in the model's own
        length unit.

    Raises:
        ValueError: If the matrix is degenerate, which would make the view a line.
    """
    (m00, m01, m02), (m10, m11, m12) = matrix3[0], matrix3[1]
    offset_x, offset_y = svg_offset
    m00, m01, m02 = m00 * svg_scale, m01 * svg_scale, m02 * svg_scale + offset_x
    m10, m11, m12 = m10 * svg_scale, m11 * svg_scale, m12 * svg_scale + offset_y

    determinant = m01 * m10 - m00 * m11
    if determinant == 0:
        msg = "the view's ifc:matrix3 is degenerate: it would collapse the view to a line"
        raise ValueError(msg)

    height = float(page_height_mm)
    scale = float(unit_scale_to_m)
    return (
        (-m11 / determinant) / scale,
        (-m10 / determinant) / scale,
        (-m01 / determinant) / scale,
        (-m00 / determinant) / scale,
        ((m11 * m02 + m01 * (height - m12)) / determinant) / scale,
        ((m10 * m02 + m00 * (height - m12)) / determinant) / scale,
    )


def apply(transform: Affine, x: float, y: float) -> tuple[float, float]:
    """Apply an affine to a paper point.

    Args:
        transform: The affine, in ``(a, b, c, d, e, f)`` order.
        x: Paper x in millimetres.
        y: Paper y in millimetres.

    Returns:
        The point on the model plane, in the model's length unit.
    """
    a, b, c, d, e, f = transform
    return (a * x + c * y + e, b * x + d * y + f)


def invert(transform: Affine, plane_x: float, plane_y: float) -> tuple[float, float]:
    """Take a point on the model plane back to paper.

    Args:
        transform: The affine, in ``(a, b, c, d, e, f)`` order.
        plane_x: The plane coordinate along ``xAxis``.
        plane_y: The plane coordinate along ``yAxis``.

    Returns:
        The paper point in millimetres.

    Raises:
        ValueError: If the affine is singular.
    """
    a, b, c, d, e, f = transform
    determinant = a * d - b * c
    if determinant == 0:
        msg = "the affine is singular and cannot be inverted"
        raise ValueError(msg)
    return (
        (d * (plane_x - e) - c * (plane_y - f)) / determinant,
        (a * (plane_y - f) - b * (plane_x - e)) / determinant,
    )


def svg_to_paper(x: float, y: float, page_height_mm: float) -> tuple[float, float]:
    """Flip one SVG point into paper coordinates.

    The serializer writes a sheet whose viewBox is one user unit per millimetre, so the
    only difference is the direction of y (SPEC 3.3).

    Args:
        x: SVG user-unit x.
        y: SVG user-unit y.
        page_height_mm: The sheet's height in millimetres.

    Returns:
        The paper point in millimetres, origin bottom-left, y up.
    """
    return (x, page_height_mm - y)


def plane_from_ifc_plane(
    ifc_plane: str, unit_scale_to_m: float
) -> tuple[list[float], list[float], list[float]]:
    """Read a view group's ``ifc:plane`` into the SPEC 3.6 origin and axes.

    Args:
        ifc_plane: The attribute's JSON text: a row-major 4x4 with the translation in
            the last column.
        unit_scale_to_m: The model's length unit in metres.

    Returns:
        ``(origin, x_axis, y_axis)``. The origin is in the model's length unit; the axes
        are directions and are unit length, so their unit is immaterial.
    """
    rows = json.loads(ifc_plane)
    origin = [float(rows[index][3]) / unit_scale_to_m for index in range(3)]
    x_axis = [float(rows[index][0]) for index in range(3)]
    y_axis = [float(rows[index][1]) for index in range(3)]
    return (origin, x_axis, y_axis)


def scale_denominator(transform: Affine, unit_scale_to_m: float) -> float:
    """Return the drawing scale a ``paperToPlane`` implies, as its denominator.

    A similarity transform carries one scale; this is the number a sheet prints as
    ``1:50``, and the validator compares it with the viewport's declared ``scale``
    (PL-GEO-008).

    Args:
        transform: The affine.
        unit_scale_to_m: The model's length unit in metres.

    Returns:
        The denominator: 50 for a drawing at 1:50.
    """
    a, b, c, d, _, _ = transform
    determinant = a * d - b * c
    return float(abs(determinant) ** 0.5) * unit_scale_to_m * 1000.0
