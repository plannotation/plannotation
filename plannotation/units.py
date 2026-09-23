# SPDX-License-Identifier: Apache-2.0
"""Units and coordinate conventions.

This module is the normative home of Plannotation's coordinate conventions in code.
The same rules appear in ``spec/SPEC.md`` §3, and the two must be kept in sync.

Paper coordinates
-----------------
Paper coordinates are **millimetres**, with the origin at the **bottom-left corner
of the page** and **y increasing upwards**. This matches the PDF user-space
convention and differs from SVG.

The page in question is the **CropBox**, falling back to the MediaBox when no
CropBox is present. The CropBox is the page as displayed and printed, and the
governing principle makes the page as seen the leading document -- so a plannotation
measures the page a person measures.

``page.widthMm`` and ``page.heightMm`` are that box's dimensions, and paper
coordinates are **unrotated**: they are expressed in the page's own space before
``/Rotate`` is applied, exactly like every other position in a PDF (MediaBox,
CropBox, annotation rectangles, the content stream). ``page.rotation`` is recorded
only so that a reader can reproduce the displayed orientation. A plannotation in
post-rotation space would be the one thing in the file needing conversion before
it could be compared with anything else.

A ``bbox`` is ``[x0, y0, x1, y1]`` in paper millimetres with ``x0 <= x1`` and
``y0 <= y1``.

PDF user space
--------------
PDF user space is measured in points, where ``1 pt = 1/72 inch``, and its axes
already agree with paper coordinates. Two things nonetheless stand between a point
and a millimetre, and ignoring either corrupts an entire plannotation silently:

* **The box origin.** A CropBox whose lower-left corner is not at the user-space
  origin must be subtracted. Large-format drawings routinely have one.
* **/UserUnit.** A page may scale user space by ``/UserUnit`` (default 1). It is
  common on large-format construction drawings -- the very drawings Plannotation
  targets -- and omitting it scales every coordinate in the plannotation.

So the conversion is::

    x_mm = (x_pt - cropBox.x0) * userUnit * MM_PER_PT
    y_mm = (y_pt - cropBox.y0) * userUnit * MM_PER_PT

:data:`MM_PER_PT` and :data:`PT_PER_MM` carry only the unit factor; the offset and
``/UserUnit`` are properties of the page and must be supplied by the caller.

SVG
---
SVG is **y-down**. Any path in or out of an SVG carrier must therefore be flipped
about the horizontal axis. For a page of height ``h`` millimetres the flip is its
own inverse::

    y_paper = h - y_svg
    y_svg = h - y_paper

Forgetting this flip is the single most common source of mirrored plannotations, so
it is applied in exactly one place per direction rather than at each call site.

Paper to model
--------------
Each viewport carries ``paperToPlane``, a 2-D affine transform stored as the
six-element list ``[a, b, c, d, e, f]`` and applied as::

    X = a * x + c * y + e
    Y = b * x + d * y + f

It maps paper millimetres to coordinates on the viewport's model plane, expressed in
the **model's** length unit (see ``model.lengthUnit``) rather than in millimetres.
The component order matches the PDF and PostScript matrix convention, so a transform
can be handed to a PDF operator unchanged.

Serialisation
-------------
Numbers are serialised with **at most three decimal places**. At drawing scales this
is far below the precision of any real drawing, and fixing it makes byte-for-byte
golden comparison possible. Plannotation JSON uses sorted keys, two-space indent, LF
line endings and UTF-8.
"""

from __future__ import annotations

import math
from typing import Final, Literal

#: Millimetres per PDF point. Exact: one point is 1/72 inch, one inch is 25.4 mm.
MM_PER_PT: Final = 25.4 / 72.0

#: PDF points per millimetre. The reciprocal of :data:`MM_PER_PT`.
PT_PER_MM: Final = 72.0 / 25.4

#: Decimal places used when serialising any number into a plannotation.
COORD_DECIMALS: Final = 3

#: Identity affine transform in ``[a, b, c, d, e, f]`` order.
IDENTITY_AFFINE: Final = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

#: The length units a plannotation can name, by their size in metres (SPEC 3.5).
METRES_PER_LENGTH_UNIT: Final[dict[Literal["m", "cm", "mm"], float]] = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
}


def length_unit_for(unit_scale_to_m: float) -> Literal["m", "cm", "mm"]:
    """Name a model's length unit, given its size in metres.

    The size is what ``ifcopenshell.util.unit.calculate_unit_scale`` reports for a
    model, so this is how a writer states ``model.lengthUnit`` from the model itself
    rather than from an assumption about it.

    Args:
        unit_scale_to_m: The unit in metres.

    Returns:
        ``m``, ``cm`` or ``mm``.

    Raises:
        ValueError: If it is none of those; Plannotation 0.1 names no other.

    Examples:
        >>> length_unit_for(0.001)
        'mm'
    """
    for name, size in METRES_PER_LENGTH_UNIT.items():
        if math.isclose(unit_scale_to_m, size, rel_tol=1e-9):
            return name
    msg = (
        f"the model's length unit is {unit_scale_to_m} m; Plannotation 0.1 describes models "
        "in metres, centimetres or millimetres"
    )
    raise ValueError(msg)
