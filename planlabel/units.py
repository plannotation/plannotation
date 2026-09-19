# SPDX-License-Identifier: Apache-2.0
"""Units and coordinate conventions.

This module is the normative home of PlanLabel's coordinate conventions in code.
The same rules appear in ``spec/SPEC.md`` §3, and the two must be kept in sync.

Paper coordinates
-----------------
Paper coordinates are **millimetres**, with the origin at the **bottom-left corner
of the page** and **y increasing upwards**. This matches the PDF user-space
convention and differs from SVG.

A ``bbox`` is ``[x0, y0, x1, y1]`` in paper millimetres with ``x0 <= x1`` and
``y0 <= y1``.

PDF user space
--------------
PDF user space is measured in points, where ``1 pt = 1/72 inch``. Conversions use
:data:`MM_PER_PT` and :data:`PT_PER_MM`; the origin and axis directions already
agree, so only a scale factor is involved.

SVG
---
SVG is **y-down**. Any path in or out of an SVG carrier must therefore be flipped
about the horizontal axis. For a page of height ``h`` millimetres the flip is its
own inverse::

    y_paper = h - y_svg
    y_svg = h - y_paper

Forgetting this flip is the single most common source of mirrored labels, so it is
applied in exactly one place per direction rather than at each call site.

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
golden comparison possible. Label JSON uses sorted keys, two-space indent, LF line
endings and UTF-8.
"""

from __future__ import annotations

from typing import Final

#: Millimetres per PDF point. Exact: one point is 1/72 inch, one inch is 25.4 mm.
MM_PER_PT: Final = 25.4 / 72.0

#: PDF points per millimetre. The reciprocal of :data:`MM_PER_PT`.
PT_PER_MM: Final = 72.0 / 25.4

#: Decimal places used when serialising any number into a label.
COORD_DECIMALS: Final = 3

#: Identity affine transform in ``[a, b, c, d, e, f]`` order.
IDENTITY_AFFINE: Final = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
