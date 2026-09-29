# SPDX-License-Identifier: Apache-2.0
"""The small geometric vocabulary every rule in this package is written in.

Nothing here knows what a plannotation is. These are the six operations the
specification's section 3 keeps reaching for -- a cross product, a dot product, an
affine applied to a paper point, a determinant, a box that contains a box, the length of
a path -- factored out so that exactly one piece of code performs each of them.

That matters more than it looks. The rules these support are the ones whose
disagreements are invisible: two implementations of "is this box inside that box" that
differ only in whether the tolerance is applied to the inner box or the outer will
agree on every document anybody tries and disagree on the one that matters. This module
is also imported by ``tools/check_fixtures.py``, which is how the fixture checker and
the validator are kept from drifting apart on the arithmetic they share.

Coordinates are as section 3.1 defines them throughout: paper millimetres, origin at
the bottom-left of the page, y up. Model-space vectors are in the model's own length
unit, which these functions never need to know.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Floating-point slack on a comparison made exactly at a tolerance, relative to the
#: size of the numbers compared. Far below anything three decimals can express.
_FLOAT_SLACK = 1e-9

__all__ = [
    "apply_affine",
    "bbox_centre",
    "bbox_corners",
    "contains",
    "cross",
    "determinant",
    "dot",
    "is_similarity",
    "norm",
    "polyline_bbox",
    "polyline_length",
    "world_point",
]


def cross(u: Sequence[float], v: Sequence[float]) -> tuple[float, float, float]:
    """Return the cross product of two three-vectors.

    Args:
        u: The left operand.
        v: The right operand.

    Returns:
        ``u x v``. For a plane's ``xAxis`` and ``yAxis`` this is the view normal of
        specification 3.6, pointing from the plane towards the observer.
    """
    return (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )


def dot(u: Sequence[float], v: Sequence[float]) -> float:
    """Return the dot product of two three-vectors.

    Args:
        u: The left operand.
        v: The right operand.

    Returns:
        The scalar product.
    """
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def norm(u: Sequence[float]) -> float:
    """Return the Euclidean length of a three-vector.

    Args:
        u: The vector.

    Returns:
        Its length.
    """
    return math.sqrt(dot(u, u))


def apply_affine(matrix: Sequence[float], x: float, y: float) -> tuple[float, float]:
    """Map a paper point through a ``paperToPlane`` transform.

    The component order is the PDF and PostScript matrix convention of specification
    3.5, not the row-major order of a textbook: ``b`` is the second component of the
    image of the x axis, not the first component of the image of the y axis.

    Args:
        matrix: The affine ``[a, b, c, d, e, f]``.
        x: The paper x coordinate, in millimetres.
        y: The paper y coordinate, in millimetres.

    Returns:
        The point on the viewport's model plane, in the model's length unit, as
        ``X = a*x + c*y + e``, ``Y = b*x + d*y + f``.
    """
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def determinant(matrix: Sequence[float]) -> float:
    """Return the determinant of a ``paperToPlane`` transform's linear part.

    Args:
        matrix: The affine ``[a, b, c, d, e, f]``.

    Returns:
        ``a*d - b*c``. Zero collapses the viewport to a line and cannot be inverted;
        a negative value mirrors the view. Specification 3.5 forbids both.
    """
    a, b, c, d, _e, _f = matrix
    return a * d - b * c


def is_similarity(matrix: Sequence[float], *, spread: float = 0.0) -> bool:
    """Report whether an affine's linear part is a similarity, to within a spread.

    A similarity is a uniform scale with a rotation, possibly with a reflection, which
    is what an authoring tool produces for an ordinary viewport. Specification 3.5
    recovers the drawing scale from one and says a validator MUST NOT check ``scale``
    against a transform that is not one, because then no scale can be recovered at all.

    In the ``[a, b, c, d]`` order of 3.5 a rotation has ``a = d`` and ``c = -b``, and
    a reflection ``a = -d`` and ``c = b``. Some similarity lies within ``spread`` of
    every coefficient exactly when one of those pairs of equations holds to within
    twice the spread, which is the test made here. A serialised transform is rounded
    by 3.8, and the spread is how far that rounding may have moved each coefficient.

    Args:
        matrix: The affine ``[a, b, c, d, e, f]``.
        spread: How far each coefficient may be from the similarity's.

    Returns:
        True when some similarity lies within the spread of every coefficient. The
        zero matrix is not one: it has no scale to recover.
    """
    a, b, c, d, _e, _f = matrix
    size = max(abs(a), abs(b), abs(c), abs(d))
    if size == 0.0:
        return False
    # Two serialised coefficients routinely differ by exactly twice the spread, since
    # both sit on the three-decimal grid, and binary floating point must not decide
    # which side of the boundary that falls: 0.049 - 0.048 is 0.0010000000000000009.
    reach = 2.0 * spread + _FLOAT_SLACK * size
    rotation = abs(a - d) <= reach and abs(b + c) <= reach
    reflection = abs(a + d) <= reach and abs(b - c) <= reach
    return rotation or reflection


def bbox_centre(box: Sequence[float]) -> tuple[float, float]:
    """Return the centre of a bbox.

    Args:
        box: ``[x0, y0, x1, y1]`` in paper millimetres.

    Returns:
        The centre point, which is where an annotation's mark sits.
    """
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def bbox_corners(box: Sequence[float]) -> list[tuple[float, float]]:
    """Return the four corners of a bbox.

    Args:
        box: ``[x0, y0, x1, y1]`` in paper millimetres.

    Returns:
        The corners, anticlockwise from the bottom left.
    """
    return [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]


def contains(outer: Sequence[float], inner: Sequence[float], *, tolerance: float = 0.0) -> bool:
    """Report whether one bbox lies inside another.

    The tolerance is applied by growing the **outer** box, never by shrinking the inner
    one. The two are not the same when the inner box is degenerate -- a horizontal grid
    line has ``y0 == y1`` -- and growing the outer box is the reading that matches
    specification 3.4, where the tolerance is a slack on the containment and not a
    licence to trim what is contained.

    Args:
        outer: The containing bbox, ``[x0, y0, x1, y1]``.
        inner: The contained bbox, in the same form.
        tolerance: How far ``inner`` may stick out, in paper millimetres.

    Returns:
        True when every edge of ``inner`` is within ``outer``, to within the tolerance.
    """
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def polyline_bbox(points: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    """Return the bounding box of a polyline.

    Args:
        points: Two or more points in paper millimetres.

    Returns:
        ``(x0, y0, x1, y1)`` enclosing every point.
    """
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def polyline_length(points: Sequence[Sequence[float]]) -> float:
    """Return the path length of a polyline.

    The path and not the chord: a dimension line with a dog-leg is drawn to its full
    run, and measuring first point to last would let any amount of it disappear into
    the corner.

    Args:
        points: Two or more points in paper millimetres.

    Returns:
        The sum of the segment lengths.
    """
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(points))


def world_point(
    origin: Sequence[float],
    x_axis: Sequence[float],
    y_axis: Sequence[float],
    plane_point: tuple[float, float],
) -> tuple[float, float, float]:
    """Map a point on a viewport's plane into model space.

    Implements ``P = origin + X*xAxis + Y*yAxis`` from specification 3.6.

    Args:
        origin: The plane's origin, in the model's length unit.
        x_axis: The plane's x axis, a unit vector.
        y_axis: The plane's y axis, a unit vector.
        plane_point: ``(X, Y)``, the output of ``paperToPlane``.

    Returns:
        The point in the model's coordinates, in the model's length unit.
    """
    plane_x, plane_y = plane_point
    coordinates = [
        origin[axis] + plane_x * x_axis[axis] + plane_y * y_axis[axis] for axis in range(3)
    ]
    return (coordinates[0], coordinates[1], coordinates[2])
