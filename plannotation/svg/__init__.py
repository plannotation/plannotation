# SPDX-License-Identifier: Apache-2.0
"""SVG: read the IFC identity an IfcOpenShell SVG carries, and plannotate a PDF from it.

Plannotation 0.1 writes nothing into an SVG; its payload encoding is reserved (SPEC
6.4.4). What is here reads the markers IfcOpenShell's serializer already writes --
product GlobalIds and classes, view transforms -- and builds an ``authored`` plannotation
from them for the PDF the same tool rendered from that SVG.
"""

from __future__ import annotations

from plannotation.svg.carrier import SvgProduct, SvgSheet, SvgView, parse_svg, read_svg
from plannotation.svg.derive import SheetSource, attach_from_svg, derive_plannotation

__all__ = [
    "SheetSource",
    "SvgProduct",
    "SvgSheet",
    "SvgView",
    "attach_from_svg",
    "derive_plannotation",
    "parse_svg",
    "read_svg",
]
