# SPDX-License-Identifier: Apache-2.0
"""Read and write PlanLabel data inside an SVG.

Compatible with the IfcOpenShell v0.8 SVG serializer: ``id="product-<GlobalId>"``
and IFC class names on ``class`` are read but never renamed. PlanLabel data is added
only as ``data-planlabel-*`` attributes or a ``<metadata>`` block.

Implemented in Phase 2.
"""

from __future__ import annotations
