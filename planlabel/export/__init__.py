# SPDX-License-Identifier: Apache-2.0
"""Headless authored export: an IFC model in, a labelled drawing sheet out.

:mod:`planlabel.export.models` builds the seeded sample models,
:mod:`planlabel.export.svg_render` draws them with IfcOpenShell's SVG serializer,
:mod:`planlabel.export.ifc_svg_pdf` composes the sheet and computes its label from the
model, and :mod:`planlabel.export.to_pdf` converts it. The exporter needs the ``ifc``
and ``svg`` extras; importing this package does not.
"""

from __future__ import annotations
