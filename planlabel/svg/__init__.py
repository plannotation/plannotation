# SPDX-License-Identifier: Apache-2.0
"""SVG: read the IFC identity an IfcOpenShell SVG carries, and label a PDF from it.

PlanLabel 0.1 writes nothing into an SVG; its payload encoding is reserved (SPEC
6.4.4). What is here reads the markers IfcOpenShell's serializer already writes --
product GlobalIds and classes, view transforms -- and builds an ``authored`` page label
from them for the PDF the same tool rendered from that SVG.
"""

from __future__ import annotations

from planlabel.svg.carrier import SvgProduct, SvgSheet, SvgView, parse_svg, read_svg
from planlabel.svg.label import SheetSource, attach_from_svg, derive_label

__all__ = [
    "SheetSource",
    "SvgProduct",
    "SvgSheet",
    "SvgView",
    "attach_from_svg",
    "derive_label",
    "parse_svg",
    "read_svg",
]
