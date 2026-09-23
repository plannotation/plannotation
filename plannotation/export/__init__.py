# SPDX-License-Identifier: Apache-2.0
"""Headless authored export: an IFC model in, a plannotated drawing sheet out.

:mod:`plannotation.export.models` builds the seeded sample models,
:mod:`plannotation.export.svg_render` draws them with IfcOpenShell's SVG serializer,
:mod:`plannotation.export.ifc_svg_pdf` composes the sheet and computes its plannotation
from the model, and :mod:`plannotation.export.to_pdf` converts it. The exporter needs the
``ifc`` and ``svg`` extras; importing this package does not.
"""

from __future__ import annotations
