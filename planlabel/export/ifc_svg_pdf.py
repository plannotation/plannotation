# SPDX-License-Identifier: Apache-2.0
"""Authored exporter: IFC to SVG to PDF, with labels.

Renders views with the IfcOpenShell SVG serializer, composes a sheet, converts to PDF
with CairoSVG, derives paper geometry from the SVG, and emits L3 labels whose
provenance is ``authored`` because every value is computed from the model.

Implemented in Phase 4.
"""

from __future__ import annotations
